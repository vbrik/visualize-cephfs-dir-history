// Page controller: loads data files, keeps view state in the URL hash, and
// renders the controls and the shown view: Units (the stacked chart, its
// legend and its table) or Watched (trend cards and their range slider).

import { rangeSliderOption } from "./charts/rangeSlider.js";
import { stackedAreaOption } from "./charts/stackedArea.js";
import { trendCardOption } from "./charts/trendCard.js";
import {
  MAX_BANDS,
  ROOT,
  decodeValue,
  encodeValue,
  formatBytes,
  formatDelta,
  hideKey,
  indexRange,
  isUnder,
  layerAt,
  parseSize,
  stack,
  trendCards,
} from "./model.js";

// How often to look for regenerated data files, in ms.
const REFRESH_MS = 10 * 60 * 1000;
const DEFAULTS = {
  tree: null,
  view: "units",
  group: 0,
  in: ROOT,
  top: MAX_BANDS,
  min: "",
  hide: [],
  rest: false,
};

const $ = (id) => document.getElementById(id);
const chart = echarts.init($("chart"), null, { renderer: "canvas" });
let index = [];
let data = null; // the current tree file
let range = null; // [a, b] point indexes shown by the zoom slider
let model = null; // the bands drawn, from model.stack
const slots = new Map(); // view key -> Map(band key -> palette slot)
// The Watched view's charts, made when it is first shown: ECharts sizes a
// chart from its element, which is 0×0 while hidden.
let slider = null;
const cardEls = new Map(); // watched path -> its card element

// --- state <-> URL hash ---------------------------------------------------

function readState() {
  const p = parseHash(location.hash.slice(1));
  return {
    tree: p.has("tree") ? Number(p.get("tree")) : (index[0]?.id ?? null),
    view: p.get("view") === "watched" ? "watched" : "units",
    group: clampInt(p.get("group"), 0, 16, DEFAULTS.group),
    in: p.get("in") || DEFAULTS.in,
    top: clampInt(p.get("top"), 1, MAX_BANDS, DEFAULTS.top),
    min: p.get("min") ?? DEFAULTS.min,
    hide: p.getAll("hide"),
    rest: p.get("rest") === "1",
  };
}

function writeState(changes) {
  const s = { ...readState(), ...changes };
  const p = [["tree", s.tree]];
  if (s.view !== DEFAULTS.view) p.push(["view", s.view]);
  if (s.group !== DEFAULTS.group) p.push(["group", s.group]);
  if (s.in !== ROOT) p.push(["in", s.in]);
  if (s.top !== DEFAULTS.top) p.push(["top", s.top]);
  if (s.min) p.push(["min", s.min]);
  for (const h of s.hide) p.push(["hide", h]);
  if (s.rest) p.push(["rest", "1"]);
  location.hash = p.map(([k, v]) => `${k}=${encodeValue(String(v))}`).join("&");
}

/** The hash's key=value pairs as a URLSearchParams-like reader; values may
 * hold paths that are not UTF-8 (see model.encodeValue). */
function parseHash(text) {
  const pairs = text
    .split("&")
    .filter(Boolean)
    .map((kv) => {
      const at = kv.indexOf("=");
      return at < 0 ? [kv, ""] : [kv.slice(0, at), decodeValue(kv.slice(at + 1))];
    });
  return {
    has: (k) => pairs.some(([key]) => key === k),
    get: (k) => pairs.find(([key]) => key === k)?.[1] ?? null,
    getAll: (k) => pairs.filter(([key]) => key === k).map(([, v]) => v),
  };
}

function clampInt(text, lo, hi, fallback) {
  const n = Number.parseInt(text, 10);
  return Number.isNaN(n) ? fallback : Math.min(hi, Math.max(lo, n));
}

// --- data -------------------------------------------------------------------

async function fetchJson(file) {
  const r = await fetch(`data/${file}`, { cache: "no-cache" });
  if (!r.ok) throw new Error(`data/${file}: HTTP ${r.status}`);
  return r.json();
}

/** Tree TREEID's file and zoom range, or null if DATA is its latest. */
async function load(treeId) {
  const entry = index.find((t) => t.id === treeId);
  if (!entry) throw new Error(`no tree ${treeId} in data/trees.json`);
  if (data?.tree.id === treeId && data.generated === entry.generated) return null;
  const fresh = await fetchJson(entry.file);
  if (data?.tree.id !== treeId || range === null || range[0] === 0) {
    return { data: fresh, range: [0, fresh.times.length - 1] };
  }
  // Keep a zoomed start's date, not its index: as history grows, older
  // points merge into coarser buckets. The end follows new data.
  return { data: fresh, range: indexRange(fresh.times, Date.parse(data.times[range[0]]), Infinity) };
}

let updates = 0; // updates started; only the latest may change data or the DOM
let indexing = null; // the trees.json fetch in flight, if any

/** Load what the URL asks for (and the tree index first, if REINDEX) and
 * render it; show an error instead if loading fails, clear it if not. An
 * update overtaken by a later one, e.g. on a quick tree switch, drops its
 * results, whatever order the fetches finish in; so every update waits for
 * an index fetch in flight rather than use the index it would replace. */
async function update(reindex) {
  const id = ++updates;
  if (reindex) indexing = fetchJson("trees.json");
  const pending = indexing;
  try {
    if (pending) {
      const trees = await pending;
      if (indexing === pending) [index, indexing] = [trees, null];
    }
    if (id !== updates) return;
    const s = readState();
    const loaded = s.tree === null ? null : await load(s.tree);
    if (id !== updates) return;
    if (loaded) ({ data, range } = loaded);
    $("error").hidden = true;
    render();
  } catch (e) {
    if (indexing === pending) indexing = null; // a failed fetch must not block later updates
    if (id === updates) showError(e);
  }
}

const refresh = () => update(true);

// --- rendering --------------------------------------------------------------

function colors() {
  const css = getComputedStyle(document.documentElement);
  const v = (name) => css.getPropertyValue(name).trim();
  return {
    palette: Array.from({ length: MAX_BANDS }, (_, i) => v(`--series-${i + 1}`)),
    smaller: v("--band-smaller"),
    rest: v("--band-rest"),
    surface: v("--surface"),
    ink: v("--ink"),
    muted: v("--muted"),
    grid: v("--grid"),
    grow: v("--grow"),
    growFill: v("--grow-fill"),
    shrink: v("--shrink"),
    shrinkFill: v("--shrink-fill"),
    flat: v("--flat"),
    flatFill: v("--flat-fill"),
  };
}

/** Whether any tree has watched dirs, i.e. the Watched view exists. */
const anyWatched = () => index.some((t) => t.watched > 0);

/** Colors per band key: survivors keep their slot, newcomers take free ones. */
function bandColors(viewKey, bands, palette) {
  const old = slots.get(viewKey) ?? new Map();
  const next = new Map();
  for (const b of bands) if (old.has(b.key)) next.set(b.key, old.get(b.key));
  const free = palette.map((_, i) => i).filter((i) => ![...next.values()].includes(i));
  for (const b of bands) if (!next.has(b.key)) next.set(b.key, free.shift());
  slots.set(viewKey, next);
  return new Map([...next].map(([k, i]) => [k, palette[i]]));
}

function render() {
  const s = readState();
  const view = s.view === "watched" && anyWatched() ? "watched" : "units";
  document.body.dataset.view = view;
  renderControls(s, view);
  if (!data) return;
  if (view === "watched") renderWatched();
  else renderUnits(s);
}

function renderUnits(s) {
  chart.resize(); // it may have been hidden, and so 0×0
  const minBytes = parseSize(s.min || "0");
  $("min").setAttribute("aria-invalid", minBytes === null);
  const inPath = Object.hasOwn(data.rbytes, s.in) ? s.in : ROOT;

  model = stack(data, {
    inPath,
    group: s.group,
    top: s.top,
    min: minBytes ?? 0,
    hidden: s.hide,
    range,
  });
  const c = colors();
  c.bands = bandColors(`${data.tree.id}|${s.group}|${inPath}`, model.bands, c.palette);

  // A tooltip left open over series that are being replaced would throw.
  chart.dispatchAction({ type: "hideTip" });
  chart.setOption(stackedAreaOption(data, model, { colors: c, showRest: s.rest, range }), {
    replaceMerge: ["series"],
  });
  zoomTo(chart);

  renderCrumbs(inPath);
  renderLegend(model, c, s);
  renderTable(model, c, s);
  renderChips(s.hide);
  renderSummary(inPath);
}

function renderControls(s, view) {
  $("view").hidden = !anyWatched();
  for (const b of $("view").querySelectorAll("button")) {
    b.setAttribute("aria-pressed", b.dataset.view === view);
  }
  const tree = $("tree");
  tree.replaceChildren(
    ...index.map((t) => {
      const o = new Option(`${t.label} — ${t.units} units`, t.id, false, t.id === s.tree);
      o.title = t.detail;
      return o;
    }),
  );
  const group = $("group");
  const levels = data ? data.tree.max_depth : 0;
  group.replaceChildren(
    new Option("none: one band per unit", 0),
    ...Array.from({ length: levels }, (_, i) => new Option(`${i + 1} level${i ? "s" : ""} down`, i + 1)),
  );
  group.value = String(Math.min(s.group, levels));
  $("top").value = s.top;
  $("top").max = MAX_BANDS;
  if (document.activeElement !== $("min")) $("min").value = s.min;
  $("rest").checked = s.rest;
}

function renderCrumbs(inPath) {
  const parts = inPath === ROOT ? [] : inPath.split("/");
  const crumbs = [button(data.tree.fs_path, () => writeState({ in: ROOT }), inPath === ROOT)];
  parts.forEach((name, i) => {
    const path = parts.slice(0, i + 1).join("/");
    crumbs.push(text(" / "), button(name, () => writeState({ in: path }), i === parts.length - 1));
  });
  $("crumbs").replaceChildren(...crumbs);
}

function renderLegend(model, c, s) {
  const [, b] = range;
  const items = model.bands.map((band) => {
    const li = document.createElement("li");
    const name = band.drillable
      ? button(band.label, () => writeState({ in: band.key }))
      : text(band.label);
    if (band.drillable) name.title = `Show the units in ${band.key}`;
    li.append(swatch(c.bands.get(band.key)), name, value(band.values[b]), hideButton(hideKey(band), s));
    return li;
  });
  const smaller = document.createElement("li");
  smaller.append(swatch(c.smaller), text(`Smaller units`), value(model.smaller[b]));
  items.push(smaller);
  if (s.rest) {
    const rest = document.createElement("li");
    rest.append(swatch(c.rest), text("Rest of tree"), value(model.rest[b]));
    items.push(rest);
  }
  $("legend").replaceChildren(...items.reverse());
}

function renderTable(model, c, s) {
  const [a, b] = range;
  const rows = model.bands.map((band) => [band.label, band.values]);
  rows.push(["Smaller units", model.smaller]);
  if (s.rest) rows.push(["Rest of tree", model.rest]);
  $("table-body").replaceChildren(
    ...rows.map(([label, values]) => {
      const tr = document.createElement("tr");
      for (const cell of [
        label,
        formatBytes(values[a]),
        formatBytes(values[b]),
        formatDelta(values[b] - values[a]),
        formatBytes(Math.max(...values.slice(a, b + 1))),
      ]) {
        const td = document.createElement("td");
        td.textContent = cell;
        tr.append(td);
      }
      return tr;
    }),
  );
  $("table-range").textContent =
    `${new Date(data.times[a]).toLocaleString()} – ${new Date(data.times[b]).toLocaleString()}`;
}

function renderChips(hide) {
  const chips = hide.map((path) => {
    const chip = button(`${path} ✕`, () => writeState({ hide: hide.filter((h) => h !== path) }));
    chip.className = "chip";
    chip.title = "Show again";
    return chip;
  });
  $("hidden").hidden = chips.length === 0;
  $("hidden-list").replaceChildren(...chips);
}

/** PATH (relative to the tree's ROOT) as an absolute path. */
function absolute(path) {
  return path === ROOT ? data.tree.fs_path : `${data.tree.fs_path}/${path}`;
}

function renderSummary(inPath) {
  const b = range[1];
  const total = data.rbytes[inPath][b] ?? 0;
  const units = Object.keys(data.units).filter((u) => isUnder(u, inPath));
  const where = absolute(inPath);
  $("summary").textContent =
    `${where}: ${formatBytes(total)} in total, ${units.length ? `${units.length} units` : "no units here"}`
    + ` (own size changed by ≥ ${formatBytes(data.threshold)}). Data generated ${new Date(data.generated).toLocaleString()}.`;
  $("chart").setAttribute(
    "aria-label",
    `Stacked area of own sizes under ${where}; values are in the table below.`,
  );
}

function renderWatched() {
  const [a, b] = range;
  const cards = trendCards(data, range);
  const c = colors();
  $("watch-note").hidden = cards.length > 0;
  $("watch-range").textContent = cards.length
    ? `${new Date(data.times[a]).toLocaleString()} – ${new Date(data.times[b]).toLocaleString()}`
    : "";

  const shown = new Set(cards.map((card) => card.path));
  for (const [path, el] of cardEls) {
    if (!shown.has(path)) {
      echarts.getInstanceByDom(el.querySelector(".spark"))?.dispose();
      cardEls.delete(path);
    }
  }
  $("cards").replaceChildren(...cards.map((card) => cardEls.get(card.path) ?? newCard(card.path)));
  for (const card of cards) {
    const el = cardEls.get(card.path);
    el.title = absolute(card.path);
    el.querySelector(".name").textContent = card.path === ROOT ? data.tree.fs_path : card.path;
    el.querySelector(".size").textContent = card.values[b] === null ? "absent" : formatBytes(card.end);
    const tag = el.querySelector(".tag");
    tag.textContent = formatDelta(card.change);
    tag.className = `tag ${card.change > 0 ? "grow" : card.change < 0 ? "shrink" : "flat"}`;
    el.querySelector(".scale").textContent =
      card.lo === null
        ? "absent throughout"
        : `scale ${formatBytes(card.lo)} – ${formatBytes(card.hi)}, span ${formatBytes(card.hi - card.lo)}`;
    const spark = el.querySelector(".spark");
    const sparkChart = echarts.getInstanceByDom(spark) ?? echarts.init(spark, null, { renderer: "svg" });
    sparkChart.dispatchAction({ type: "hideTip" }); // an open tooltip would throw
    sparkChart.setOption(trendCardOption(data, card, { colors: c, range }), { notMerge: true });
  }

  if (!slider) {
    slider = echarts.init($("watch-slider"), null, { renderer: "canvas" });
    slider.on("datazoom", () => followZoom(slider));
  }
  slider.resize();
  slider.setOption(rangeSliderOption(data, { colors: c }), { replaceMerge: ["series"] });
  zoomTo(slider);
}

/** A trend card element for PATH; its content is filled by renderWatched. */
function newCard(path) {
  const el = document.createElement("div");
  el.className = "trend-card";
  el.tabIndex = 0;
  el.setAttribute("role", "link");
  const head = document.createElement("div");
  head.className = "trend-head";
  for (const cls of ["name", "size", "tag"]) head.append(Object.assign(text(""), { className: cls }));
  const spark = document.createElement("div");
  spark.className = "spark";
  el.append(head, spark, Object.assign(text(""), { className: "scale" }));
  const open = () => writeState({ view: "units", in: path });
  el.addEventListener("click", open);
  el.addEventListener("keydown", (e) => e.key === "Enter" && open());
  cardEls.set(path, el);
  return el;
}

/** Set TARGET chart's zoom slider to the shown range. */
function zoomTo(target) {
  target.dispatchAction({
    type: "dataZoom",
    startValue: Date.parse(data.times[range[0]]),
    endValue: Date.parse(data.times[range[1]]),
  });
}

/** Take the range from SOURCE's zoom slider; re-render if it changed. Both
 * views' sliders call this, and render sets the shown one back to the
 * range, which changes nothing, so the two never loop. */
function followZoom(source) {
  const [z] = source.getOption().dataZoom;
  const next = indexRange(data.times, z.startValue, z.endValue);
  if (next[0] !== range[0] || next[1] !== range[1]) {
    range = next;
    render();
  }
}

function showError(e) {
  $("error").hidden = false;
  $("error").textContent = `Could not load data: ${e.message}. Has dirhistory-generate run?`;
}

// --- small DOM helpers (labels are untrusted: textContent only) -------------

function text(s) {
  const span = document.createElement("span");
  span.textContent = s;
  return span;
}

function button(label, onClick, current = false) {
  const b = document.createElement("button");
  b.type = "button";
  b.className = "link";
  b.textContent = label;
  if (current) b.setAttribute("aria-current", "true");
  b.addEventListener("click", onClick);
  return b;
}

function swatch(color) {
  const s = document.createElement("span");
  s.className = "swatch";
  s.style.background = color;
  return s;
}

function value(bytes) {
  const v = text(formatBytes(bytes));
  v.className = "value";
  return v;
}

function hideButton(path, s) {
  const b = button("hide", () => writeState({ hide: [...s.hide, path] }));
  b.className = "hide";
  b.title = path.endsWith("/") ? `Remove all units in ${path}` : `Remove ${path}`;
  return b;
}

// --- wiring -----------------------------------------------------------------

$("tree").addEventListener("change", (e) =>
  writeState({ tree: Number(e.target.value), in: ROOT, hide: [] }),
);
$("group").addEventListener("change", (e) => writeState({ group: Number(e.target.value) }));
$("top").addEventListener("change", (e) => writeState({ top: clampInt(e.target.value, 1, MAX_BANDS, MAX_BANDS) }));
$("min").addEventListener("change", (e) => writeState({ min: e.target.value.trim() }));
$("rest").addEventListener("change", (e) => writeState({ rest: e.target.checked }));
$("reset").addEventListener("click", () => writeState({ ...DEFAULTS, tree: readState().tree }));

// Clicking a group's area drills into it. ECharts reports clicks on a line
// series' line only, so find the band under the pointer from the stack.
chart.getZr().on("click", (e) => {
  if (!model || !chart.containPixel("grid", [e.offsetX, e.offsetY])) return;
  const [ms, bytes] = chart.convertFromPixel("grid", [e.offsetX, e.offsetY]);
  const band = model.bands[layerAt(model.bands.map((b) => b.values), data.times, ms, bytes)];
  if (band?.drillable) writeState({ in: band.key });
});
chart.on("datazoom", () => followZoom(chart));
for (const b of $("view").querySelectorAll("button")) {
  b.addEventListener("click", () => writeState({ view: b.dataset.view }));
}

window.addEventListener("hashchange", () => update(false));
window.addEventListener("resize", () => {
  chart.resize();
  slider?.resize();
  for (const el of cardEls.values()) echarts.getInstanceByDom(el.querySelector(".spark"))?.resize();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => render());
setInterval(refresh, REFRESH_MS);
refresh();
