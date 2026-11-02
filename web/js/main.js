// Page controller: loads data files, keeps view state in the URL hash, and
// renders the controls and the shown view of the chosen set: Stack (the
// stacked chart of its dirs' bands, its legend and its table) or Trends (its
// dirs' trend cards and their range slider).

import { rangeSliderOption } from "./charts/rangeSlider.js";
import { stackedAreaOption } from "./charts/stackedArea.js";
import { HEIGHT, WIDTH, sparkPaths, sparkPointAt, sparkXs } from "./charts/trendCard.js";
import {
  CARD_SORTS,
  LEGEND_SORTS,
  MAX_BANDS,
  ROOT,
  decodeValue,
  encodeValue,
  formatBytes,
  formatDelta,
  indexRange,
  layerAt,
  maxDepth,
  normalize,
  reachable,
  relative,
  setDirOf,
  sortCards,
  sortLegend,
  stackBands,
  trendCards,
  unchanged,
} from "./model.js";

// How often to look for regenerated data files, in ms.
const REFRESH_MS = 10 * 60 * 1000;
const DEFAULTS = {
  tree: null,
  view: "stack",
  set: null, // the tree's first set, "Top level"
  depth: 0,
  top: MAX_BANDS,
  in: "", // the drilled-into dir; "": none, the set's dirs are shown
  hide: [],
  // The legend's ticked band keys; null: all are ticked (no picking).
  pick: null,
  lsort: "", // the legend's order (see model.sortLegend); "": chart order
  sort: "span",
  flat: false,
};
// The Stack view's state keys, which its Reset restores.
const STACK_KEYS = ["depth", "top", "in", "hide", "pick", "lsort"];
// Picks are band keys of one level's legend: changing level clears them.
const NEW_LEVEL = { pick: null };
const SVG = "http://www.w3.org/2000/svg";

const $ = (id) => document.getElementById(id);
const chart = echarts.init($("chart"), null, { renderer: "canvas" });
let index = [];
let data = null; // the current tree file
let range = null; // [a, b] point indexes shown by the zoom slider
let model = null; // the bands listed in the legend, from model.stackBands
let drawn = []; // those of them drawn: the ticked ones
const slots = new Map(); // view key -> Map(band key -> palette slot)
// The Trends view's slider, made when it is first shown: ECharts sizes a
// chart from its element, which is 0×0 while hidden.
let slider = null;
const cardEls = new Map(); // set dir -> its card element
const cardOf = new WeakMap(); // shown card element -> its card (model.trendCards)
let sparkX = []; // the sparklines' point x's, from sparkXs

// --- state <-> URL hash ---------------------------------------------------

// Keys of older pages (group, min, rest; view=units or watched) are ignored.
function readState() {
  const p = parseHash(location.hash.slice(1));
  return {
    tree: p.has("tree") ? Number(p.get("tree")) : (index[0]?.id ?? null),
    view: p.get("view") === "trends" ? "trends" : "stack",
    set: p.get("set") ?? DEFAULTS.set,
    // Clamped to the levels stored when rendering, which needs the data.
    depth: clampInt(p.get("depth"), 0, Number.MAX_SAFE_INTEGER, DEFAULTS.depth),
    top: clampInt(p.get("top"), 1, MAX_BANDS, DEFAULTS.top),
    in: p.get("in") ?? DEFAULTS.in,
    hide: p.getAll("hide"),
    // "pick=" alone: picking, with nothing ticked.
    pick: p.has("pick") ? p.getAll("pick").filter((k) => k !== "") : null,
    lsort: Object.hasOwn(LEGEND_SORTS, (p.get("lsort") ?? "").replace(/^-/, "")) ? p.get("lsort") : "",
    sort: Object.hasOwn(CARD_SORTS, p.get("sort")) ? p.get("sort") : DEFAULTS.sort,
    flat: p.get("flat") === "1",
  };
}

function writeState(changes) {
  const s = { ...readState(), ...changes };
  const p = [["tree", s.tree]];
  if (s.view !== DEFAULTS.view) p.push(["view", s.view]);
  if (s.set !== DEFAULTS.set) p.push(["set", s.set]);
  if (s.depth !== DEFAULTS.depth) p.push(["depth", s.depth]);
  if (s.top !== DEFAULTS.top) p.push(["top", s.top]);
  if (s.in) p.push(["in", s.in]);
  for (const h of s.hide) p.push(["hide", h]);
  if (s.pick !== null) for (const k of s.pick.length ? s.pick : [""]) p.push(["pick", k]);
  if (s.lsort) p.push(["lsort", s.lsort]);
  if (s.sort !== DEFAULTS.sort) p.push(["sort", s.sort]);
  if (s.flat) p.push(["flat", "1"]);
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
  const fresh = normalize(await fetchJson(entry.file));
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
    surface: v("--surface"),
    ink: v("--ink"),
    muted: v("--muted"),
    grid: v("--grid"),
  };
}

/** The loaded tree's set that S names, else its first ("Top level"). */
const currentSet = (s) => data.sets.find((set) => set.name === s.set) ?? data.sets[0];

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
  document.body.dataset.view = s.view;
  renderControls(s);
  if (!data) return;
  const set = currentSet(s);
  $("stack-empty").hidden = set.paths.length > 0;
  if (s.view === "trends") renderTrends(s, set);
  else renderStack(s, set);
}

/** The Stack view of SET: its dirs' bands, or those of S.in if drilling
 * can reach it from them. */
function renderStack(s, set) {
  const inPath = s.in && reachable(data, set.paths, s.in) ? s.in : null;
  renderCrumbs(set, inPath);
  $("stack-body").hidden = set.paths.length === 0;
  if (!set.paths.length) {
    model = null;
    drawn = [];
    return;
  }
  chart.resize(); // it may have been hidden, and so 0×0
  const roots = inPath === null ? set.paths : [inPath];
  const levels = maxDepth(data, roots);
  const depth = Math.min(s.depth, levels);
  $("depth").max = levels;
  $("depth").value = depth;

  const keep = s.pick ?? [];
  model = stackBands(data, { roots, base: inPath, depth, top: s.top, hidden: s.hide, range, keep });
  drawn = s.pick === null ? model.bands : model.bands.filter((band) => keep.includes(band.key));
  const c = colors();
  // Keyed without Depth and Top, so bands keep their colors when those
  // change; over the listed bands, so a band keeps its color when unticked.
  c.bands = bandColors(`${data.tree.id}|${set.name}|${inPath}`, model.bands, c.palette);

  // A tooltip left open over series that are being replaced would throw.
  chart.dispatchAction({ type: "hideTip" });
  // replaceMerge keeps a surviving series at its old index, so clear them
  // first: the stack must be in DRAWN's order, as layerAt assumes.
  chart.setOption({ series: [] }, { replaceMerge: ["series"] });
  chart.setOption(stackedAreaOption(data, { bands: drawn }, { colors: c, range }), {
    replaceMerge: ["series"],
  });
  zoomTo(chart);

  renderLegend(c, s);
  renderTable();
  renderChips(s.hide);
  renderSummary(s, set, inPath);
}

function renderControls(s) {
  for (const b of $("view").querySelectorAll("button")) {
    b.setAttribute("aria-pressed", b.dataset.view === s.view);
  }
  const tree = $("tree");
  tree.replaceChildren(
    ...index.map((t) => {
      const o = new Option(t.label, t.id, false, t.id === s.tree);
      o.title = t.detail;
      return o;
    }),
  );
  $("top").value = s.top;
  const sort = $("sort");
  if (!sort.options.length) {
    sort.append(...Object.entries(CARD_SORTS).map(([key, [label]]) => new Option(label, key)));
  }
  sort.value = s.sort;
  $("flat").checked = s.flat;
  $("set").replaceChildren(
    ...(data?.sets ?? []).map((set) => {
      const n = set.paths.length;
      return new Option(`${set.name} — ${n} dir${n === 1 ? "" : "s"}`, set.name);
    }),
  );
  $("set").value = data ? currentSet(s).name : "";
}

/** Crumbs: the set, then (when drilled) the set dir holding INPATH, as one
 * crumb, and each component below it. */
function renderCrumbs(set, inPath) {
  const crumbs = [
    button(`Set ${set.name}`, () => writeState({ in: "", depth: 0, ...NEW_LEVEL }), inPath === null),
  ];
  if (inPath !== null) {
    const top = setDirOf(set.paths, inPath);
    const below = relative(inPath, top);
    const parts = below === ROOT ? [] : below.split("/");
    const name = top === ROOT ? data.tree.fs_path : top;
    crumbs.push(text(" / "), button(name, () => drill(top), parts.length === 0));
    parts.forEach((part, i) => {
      const path = [top, ...parts.slice(0, i + 1)].filter((p) => p !== ROOT).join("/");
      crumbs.push(text(" / "), button(part, () => drill(path), i === parts.length - 1));
    });
  }
  $("crumbs").replaceChildren(...crumbs);
}

/** Show the bands inside DIR, one level down. */
const drill = (dir) => writeState({ in: dir, depth: 1, ...NEW_LEVEL });

/** The legend: the listed bands in S.lsort order, ticked ones first, each
 * with its tick box, swatch, name (a drill button if drillable), size and
 * change over the range, and hide button; and its header's sort state. */
function renderLegend(c, s) {
  const [a, b] = range;
  const items = sortLegend(model.bands, s.lsort, range, drawn).map((band) => {
    const li = document.createElement("li");
    const on = drawn.includes(band);
    li.classList.toggle("off", !on);
    const tick = document.createElement("input");
    tick.type = "checkbox";
    tick.className = "pick";
    tick.checked = on;
    tick.setAttribute("aria-label", `Draw ${band.label}`);
    tick.addEventListener("change", () => pick(band.key, tick.checked, s));
    const name = band.drillable ? button(band.label, () => drill(band.dir)) : text(band.label);
    name.classList.add("label");
    if (band.drillable) name.title = `Split ${absolute(band.dir)} into its subdirectories`;
    const delta = text(formatDelta(band.values[b] - band.values[a]));
    delta.className = "delta";
    li.append(tick, swatch(c.bands.get(band.key)), name, value(band.values[b]), delta, hideButton(band, s));
    return li;
  });
  $("legend").replaceChildren(...items);

  const key = s.lsort.replace(/^-/, "");
  const reversed = s.lsort.startsWith("-");
  for (const head of $("legend-head").querySelectorAll(".sort")) {
    const by = head.dataset.sort;
    // Names sort A to Z, values largest first; "up" is the arrow, A to Z or smallest first.
    const up = (by === "name") !== reversed;
    if (by === key) head.dataset.order = up ? "up" : "down";
    else delete head.dataset.order;
    head.title = "Sort by this column; click again to reverse, a third time for chart order";
  }
}

/** Tick (ON) or untick band KEY: picking starts from all listed bands, and
 * ends when all are ticked again. */
function pick(key, on, s) {
  const listed = model.bands.map((band) => band.key);
  const was = (s.pick ?? listed).filter((k) => listed.includes(k));
  const next = on ? [...new Set([...was, key])] : was.filter((k) => k !== key);
  writeState({ pick: listed.every((k) => next.includes(k)) ? null : next });
}

/** The legend's next order after a click on column BY's header: its own
 * order, then reversed, then chart order. */
function nextLegendSort(current, by) {
  if (current === by) return `-${by}`;
  if (current === `-${by}`) return "";
  return by;
}

function renderTable() {
  const [a, b] = range;
  $("table-body").replaceChildren(
    ...drawn.map(({ label, values }) => {
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

/** A hide-list key as a name: a dir's path, or `dir/ bytes` for a dir's own files. */
function hiddenName(key) {
  if (!key.endsWith("/.")) return key === ROOT ? data.tree.fs_path : key;
  const dir = key.slice(0, -2);
  return `${dir === ROOT ? data.tree.fs_path : dir}/ bytes`;
}

function renderChips(hide) {
  const chips = hide.map((key) => {
    const chip = button(`${hiddenName(key)} ✕`, () => writeState({ hide: hide.filter((h) => h !== key) }));
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

function renderSummary(s, set, inPath) {
  const b = range[1];
  const total = drawn.reduce((sum, band) => sum + band.values[b], 0);
  const n = drawn.length;
  const k = set.paths.length;
  const where = inPath === null ? set.name : absolute(inPath);
  // At Top's max, raising it cannot help; hiding bands or drilling in can.
  const cut = model.cut ? `, ${model.cut} more not shown${s.top < MAX_BANDS ? " (raise Top)" : ""}` : "";
  const of = drawn.length < model.bands.length ? ` of ${model.bands.length} listed` : "";
  $("summary").textContent =
    `${where}: ${formatBytes(total)} in ${n}${of} band${n === 1 && !of ? "" : "s"}${cut}; `
    + `set ${set.name}: ${k} dir${k === 1 ? "" : "s"}. Data generated ${new Date(data.generated).toLocaleString()}.`;
  $("chart").setAttribute("aria-label", `Stacked area of directory sizes in ${where}; values are in the table below.`);
}

/** The Trends view: the cards of SET's dirs, minus unchanged ones unless
 * S.flat. */
function renderTrends(s, set) {
  const [a, b] = range;
  const all = trendCards(data, set.paths, range);
  const cards = sortCards(s.flat ? all : all.filter((card) => !unchanged(card)), s.sort);
  const hidden = all.length - cards.length;
  hideSparkTip();
  $("trends-range").textContent = all.length
    ? `${new Date(data.times[a]).toLocaleString()} – ${new Date(data.times[b]).toLocaleString()}`
    : "";
  $("trends-hidden").hidden = hidden === 0;
  $("trends-hidden").textContent = cards.length === 0
    ? `All ${hidden} directories are unchanged in this range; check Show unchanged to see them.`
    : hidden === 1
      ? "1 directory unchanged in this range is hidden."
      : `${hidden} directories unchanged in this range are hidden.`;

  sparkX = sparkXs(data.times, range);
  const shown = new Set(all.map((card) => card.path));
  for (const path of cardEls.keys()) if (!shown.has(path)) cardEls.delete(path);
  $("cards").replaceChildren(...cards.map((card) => cardEls.get(card.path) ?? newCard(card.path)));
  for (const card of cards) {
    const el = cardEls.get(card.path);
    cardOf.set(el, card);
    el.className = `trend-card ${card.change > 0 ? "grow" : card.change < 0 ? "shrink" : "flat"}`;
    const name = el.querySelector(".name");
    name.textContent = card.path === ROOT ? data.tree.fs_path : card.path;
    name.title = absolute(card.path);
    el.querySelector(".size").textContent = card.values[b] === null ? "absent" : formatBytes(card.end);
    el.querySelector(".tag").textContent = formatDelta(card.change);
    el.querySelector(".scale").textContent =
      card.lo === null
        ? "absent in this range"
        : `scale ${formatBytes(card.lo)} – ${formatBytes(card.hi)}, span ${formatBytes(card.hi - card.lo)}`;
    const { line, fill } = sparkPaths(sparkX, card, range);
    el.querySelector(".line").setAttribute("d", line);
    el.querySelector(".fill").setAttribute("d", fill);
  }

  if (!slider) {
    slider = echarts.init($("trends-slider"), null, { renderer: "canvas" });
    slider.on("datazoom", () => followZoom(slider));
  }
  slider.resize();
  slider.setOption(rangeSliderOption(data, { colors: colors(), shadow: setTotal(set) }), {
    replaceMerge: ["series"],
  });
  zoomTo(slider);
}

/** SET's dirs' summed rbytes per point (absent as 0): the slider's shadow. */
function setTotal(set) {
  const total = new Array(data.times.length).fill(0);
  for (const path of set.paths) data.rbytes[path].forEach((v, i) => (total[i] += v ?? 0));
  return total;
}

/** A trend card element for PATH; its content is filled by renderTrends. */
function newCard(path) {
  const el = document.createElement("div");
  el.tabIndex = 0;
  el.setAttribute("role", "link");
  const head = document.createElement("div");
  head.className = "trend-head";
  for (const cls of ["name", "size", "tag"]) head.append(Object.assign(text(""), { className: cls }));
  const spark = svg("svg", "spark");
  spark.setAttribute("viewBox", `0 0 ${WIDTH} ${HEIGHT}`);
  spark.setAttribute("preserveAspectRatio", "none");
  spark.setAttribute("aria-hidden", "true");
  const cursor = svg("line", "cursor");
  cursor.setAttribute("y2", HEIGHT);
  spark.append(svg("path", "fill"), svg("path", "line"), cursor);
  el.append(head, spark, Object.assign(text(""), { className: "scale" }));
  // The Stack view inside the card's dir, split one level if it can be.
  const open = () =>
    writeState({ view: "stack", in: path, depth: data.split.has(path) ? 1 : 0, ...NEW_LEVEL });
  el.addEventListener("click", open);
  el.addEventListener("keydown", (e) => e.key === "Enter" && open());
  cardEls.set(path, el);
  return el;
}

// One tooltip for all sparklines, which have no chart library behind them:
// the point nearest the pointer in time, marked by its card's cursor line.
let tipCursor = null; // the cursor line shown, if any

function showSparkTip(e) {
  const spark = e.target.closest?.(".spark");
  const card = spark && cardOf.get(spark.parentElement);
  if (!card) return hideSparkTip();
  const box = spark.getBoundingClientRect();
  const [i, x] = sparkPointAt(sparkX, range, (e.clientX - box.left) / box.width);
  const cursor = spark.querySelector(".cursor");
  if (tipCursor !== cursor) hideSparkTip();
  tipCursor = cursor;
  cursor.setAttribute("x1", x);
  cursor.setAttribute("x2", x);
  cursor.style.visibility = "visible";

  const tip = $("spark-tip");
  tip.querySelector(".tt-when").textContent = new Date(data.times[i]).toLocaleString();
  tip.querySelector("b").textContent = card.values[i] === null ? "absent" : formatBytes(card.values[i]);
  tip.hidden = false;
  const left = box.left + (x / WIDTH) * box.width - tip.offsetWidth / 2;
  tip.style.left = `${Math.max(4, Math.min(innerWidth - tip.offsetWidth - 4, left))}px`;
  const above = box.top - tip.offsetHeight - 6;
  tip.style.top = `${above >= 0 ? above : box.bottom + 6}px`;
}

function hideSparkTip() {
  $("spark-tip").hidden = true;
  if (tipCursor) tipCursor.style.visibility = "";
  tipCursor = null;
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

/** An SVG element NAME of class CLS. */
function svg(name, cls) {
  const e = document.createElementNS(SVG, name);
  e.classList.add(cls);
  return e;
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

/** A button that hides BAND: its key is its hide-list key (see model.stackBands). */
function hideButton(band, s) {
  const b = button("hide", () =>
    writeState({ hide: [...s.hide, band.key], pick: s.pick?.filter((k) => k !== band.key) ?? null }),
  );
  b.className = "hide";
  b.title = band.own
    ? `Remove the files directly in ${absolute(band.dir)}`
    : `Remove ${absolute(band.dir)} and everything in it`;
  return b;
}

// --- wiring -----------------------------------------------------------------

// Enter in a field would submit the form and reload the page.
$("controls").addEventListener("submit", (e) => e.preventDefault());
// Set names, dirs and hides belong to a tree; a dir drilled into, to a set.
$("tree").addEventListener("change", (e) =>
  writeState({ tree: Number(e.target.value), in: "", hide: [], set: null, ...NEW_LEVEL }),
);
$("set").addEventListener("change", (e) => writeState({ set: e.target.value, in: "", ...NEW_LEVEL }));
$("depth").addEventListener("change", (e) =>
  writeState({ depth: clampInt(e.target.value, 0, Number.MAX_SAFE_INTEGER, DEFAULTS.depth), ...NEW_LEVEL }),
);
$("top").addEventListener("change", (e) => writeState({ top: clampInt(e.target.value, 1, MAX_BANDS, MAX_BANDS) }));
$("reset").addEventListener("click", () =>
  writeState(Object.fromEntries(STACK_KEYS.map((k) => [k, DEFAULTS[k]]))),
);
$("pick-none").addEventListener("click", () => writeState({ pick: [] }));
$("pick-all").addEventListener("click", () => writeState({ pick: null }));
for (const head of $("legend-head").querySelectorAll(".sort")) {
  head.addEventListener("click", () => writeState({ lsort: nextLegendSort(readState().lsort, head.dataset.sort) }));
}
$("sort").addEventListener("change", (e) => writeState({ sort: e.target.value }));
$("flat").addEventListener("change", (e) => writeState({ flat: e.target.checked }));
$("cards").addEventListener("pointermove", showSparkTip);
$("cards").addEventListener("pointerleave", hideSparkTip);
addEventListener("scroll", hideSparkTip, { passive: true });

// Clicking a drillable band's area drills into it. ECharts reports clicks on
// a line series' line only, so find the band under the pointer from the stack.
chart.getZr().on("click", (e) => {
  if (!model || !chart.containPixel("grid", [e.offsetX, e.offsetY])) return;
  const [ms, bytes] = chart.convertFromPixel("grid", [e.offsetX, e.offsetY]);
  const band = drawn[layerAt(drawn.map((b) => b.values), data.times, ms, bytes)];
  if (band?.drillable) drill(band.dir);
});
// With no band drawn the chart has no time extent, and its slider reports
// nonsense; the range stays as it was.
chart.on("datazoom", () => drawn.length && followZoom(chart));
for (const b of $("view").querySelectorAll("button")) {
  b.addEventListener("click", () => writeState({ view: b.dataset.view }));
}

window.addEventListener("hashchange", () => update(false));
window.addEventListener("resize", () => {
  chart.resize();
  slider?.resize();
});
matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => render());
setInterval(refresh, REFRESH_MS);
refresh();
