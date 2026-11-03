// Pure data shaping for the charts; no DOM. See docs/terms.md for "set",
// "stored dir", "metric" and "own-files band". A tree file holds, per time
// point, the rbytes and rentries of every stored dir (a set dir, or one
// within its depth below it), the tree's sets, and `split`: the stored dirs
// whose subdirs are all stored.

export const ROOT = ".";

// Most bands the Stack view draws (Top's max), and Top's default.
export const MAX_BANDS = 512;
export const DEFAULT_TOP = 64;
// Slots of the categorical palette (--series-N); past that, colors repeat.
export const PALETTE_SIZE = 64;

/** Number of path components; ROOT is 0. */
export function depth(path) {
  return path === ROOT ? 0 : path.split("/").length;
}

/** Whether PATH is BASE or inside it. */
export function isUnder(path, base) {
  return base === ROOT || path === base || path.startsWith(`${base}/`);
}

/** PATH's parent dir; ROOT for a top-level dir. */
export function parentOf(path) {
  const at = path.lastIndexOf("/");
  return at < 0 ? ROOT : path.slice(0, at);
}

/** PATH relative to BASE ("." for BASE itself). */
export function relative(path, base) {
  if (path === base) return ".";
  return base === ROOT ? path : path.slice(base.length + 1);
}

// Paths in data files are Python os.fsdecode output: a byte that is not
// UTF-8 is a lone surrogate U+DC80..U+DCFF. URLSearchParams would turn those
// into U+FFFD, so URL values use this codec, which keeps them as raw bytes.
// ignoreBOM: else a U+FEFF in a name decodes to "".
const strict = new TextDecoder("utf-8", { fatal: true, ignoreBOM: true });
const utf8 = new TextEncoder();

/** S percent-encoded as its file-name bytes ("/" kept). */
export function encodeValue(s) {
  let out = "";
  for (const ch of s) {
    const cp = ch.codePointAt(0);
    const bytes = cp >= 0xdc80 && cp <= 0xdcff ? [cp - 0xdc00] : utf8.encode(ch);
    for (const b of bytes) {
      const c = String.fromCharCode(b);
      out += /[A-Za-z0-9\-._~/]/.test(c) ? c : `%${b.toString(16).toUpperCase().padStart(2, "0")}`;
    }
  }
  return out;
}

/** The inverse of encodeValue: bytes that are not UTF-8 become U+DCxx. */
export function decodeValue(text) {
  const bytes = [];
  for (let i = 0; i < text.length; i++) {
    const hex = text[i] === "%" ? text.slice(i + 1, i + 3) : "";
    if (/^[0-9A-Fa-f]{2}$/.test(hex)) {
      bytes.push(Number.parseInt(hex, 16));
      i += 2;
    } else {
      bytes.push(...utf8.encode(text[i] === "+" ? " " : text[i]));
    }
  }
  let out = "";
  for (let i = 0; i < bytes.length; ) {
    const b = bytes[i];
    const n = b < 0x80 ? 1 : b < 0xc2 ? 0 : b < 0xe0 ? 2 : b < 0xf0 ? 3 : b < 0xf5 ? 4 : 0;
    let ch = null;
    if (n && i + n <= bytes.length) {
      try {
        ch = strict.decode(new Uint8Array(bytes.slice(i, i + n)));
      } catch {
        ch = null;
      }
    }
    out += ch ?? String.fromCharCode(0xdc00 + b);
    i += ch === null ? 1 : n;
  }
  return out;
}

/** S with HTML's special characters escaped, for ECharts tooltip HTML. */
export const escapeHtml = (s) => s.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

/** Bytes as a short IEC string, e.g. "1.2 TiB". */
export function formatBytes(bytes) {
  const units = ["B", "KiB", "MiB", "GiB", "TiB", "PiB", "EiB"];
  let v = Math.abs(bytes);
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i++;
  }
  const digits = i === 0 || v >= 100 ? 0 : v >= 10 ? 1 : 2;
  return `${bytes < 0 ? "−" : ""}${v.toFixed(digits)} ${units[i]}`;
}

/** A count as a short SI string, e.g. "950", "12.3 k", "1.20 M". */
export function formatCount(n) {
  const units = ["", " k", " M", " G", " T", " P"];
  let v = Math.abs(n);
  let i = 0;
  // From 999.5 on, so that rounding never shows "1000 k".
  while (v >= 999.5 && i < units.length - 1) {
    v /= 1000;
    i++;
  }
  const digits = i === 0 || v >= 100 ? 0 : v >= 10 ? 1 : 2;
  return `${n < 0 ? "−" : ""}${v.toFixed(digits)}${units[i]}`;
}

/** Signed change in FORMAT (default bytes), e.g. "+3.1 GiB", "±0 B". */
export function formatDelta(value, format = formatBytes) {
  return value > 0 ? `+${format(value)}` : value < 0 ? format(value) : `±${format(0)}`;
}

// Change mode's rates are per day.
export const DAY_MS = 86_400_000;

// TIMES (ISO strings) as ms, parsed once per array: a tree's times are one
// array, and rates and hit-testing run per band, card and pointer move.
const parsed = new WeakMap();
function msOf(times) {
  let ms = parsed.get(times);
  if (!ms) parsed.set(times, (ms = times.map((t) => Date.parse(t))));
  return ms;
}

/** A signed rate per day in FORMAT (default bytes), e.g. "+2.34 TiB/d". */
export const formatRate = (value, format = formatBytes) => `${formatDelta(value, format)}/d`;

/**
 * VALUES' rate of change per day at each of TIMES (ISO strings): point i's
 * is (v[i] - v[i-1]) over the time since point i-1, so it covers the
 * interval (t[i-1], t[i]] whatever its width. Absent counts as 0. Point 0
 * has none (null).
 */
export function rates(values, times) {
  const ms = msOf(times);
  return values.map((v, i) => (i === 0 ? null : (((v ?? 0) - (values[i - 1] ?? 0)) / (ms[i] - ms[i - 1])) * DAY_MS));
}

/**
 * DIFF (a subtraction's unclamped values) without CephFS's propagation-lag
 * outliers: a subdir's change can show before its parent's within a sample,
 * so the difference dips (or bumps) for one point. At each point i (not the
 * first or last) where an array of SUBTRACTED (what DIFF subtracted, absent
 * as 0) changed, the result is the median of DIFF's i-1, i and i+1, which
 * removes a one-point outlier and keeps a step; other points are copied.
 */
export function smoothLag(diff, subtracted) {
  const median3 = (x, y, z) => Math.max(Math.min(x, y), Math.min(Math.max(x, y), z));
  return diff.map((v, i) =>
    i > 0 && i < diff.length - 1 && subtracted.some((s) => s[i] !== s[i - 1])
      ? median3(diff[i - 1], v, diff[i + 1])
      : v,
  );
}

// What the views can chart, per tree file key: format (a value as short
// text, for columns), amount (with its unit, for prose), own (the own-files
// band's suffix, `dir/ <own>`), noun (what a value measures). rentries is
// rfiles + rsubdirs, so a dir counts itself, and its own-files band is the
// files directly in it plus 1.
export const METRICS = {
  rbytes: { format: formatBytes, amount: formatBytes, own: "bytes", noun: "size" },
  rentries: {
    format: formatCount,
    amount: (n) => `${formatCount(n)} entries`,
    own: "entries",
    noun: "entry count",
  },
};

/**
 * RAW, a tree file as read, ready for the views: for each METRICS key, a
 * map from every stored dir to its series, a list of one value (or null:
 * absent) per time point; `split` a Set; and `children` a Map from each
 * split dir to its stored subdirs, sorted.
 *
 * The file's `series` maps each dir to one series per metric, in the order
 * of its `metrics`; a series that never changed is one number. A metric the
 * file lacks is null. A file written before `series` has `rbytes` instead,
 * the map of rbytes series alone.
 */
export function normalize(raw) {
  const n = raw.times.length;
  const expand = (v) => (Array.isArray(v) ? v : new Array(n).fill(v));
  const { series = null, metrics: order = [], ...rest } = raw;
  const pairs = series ? Object.entries(series) : Object.entries(raw.rbytes).map(([p, v]) => [p, [v]]);
  const at = series ? order : ["rbytes"];
  const metrics = Object.fromEntries(
    Object.keys(METRICS).map((m) => {
      const i = at.indexOf(m);
      return [m, i < 0 ? null : Object.fromEntries(pairs.map(([p, v]) => [p, expand(v[i])]))];
    }),
  );
  const split = new Set(raw.split);
  const children = new Map([...split].map((dir) => [dir, []]));
  for (const p of Object.keys(metrics.rbytes)) {
    if (p !== ROOT) children.get(parentOf(p))?.push(p);
  }
  for (const kids of children.values()) kids.sort();
  return { ...rest, ...metrics, split, children };
}

/**
 * The Stack view's bands for DATA (from normalize).
 *
 * opts: metric (a METRICS key, default "rbytes": the series to stack),
 * roots (the dirs to show: a set's dirs, or the drilled-into dir),
 * base (labels are relative to it; null: the tree root), depth (levels to
 * split below each root), top (most bands returned), hidden (band keys; see
 * below), range ([first, last] point indexes), by (optional; a legend sort,
 * see sortLegend: which bands TOP keeps), min and flat (optional filters,
 * see passes; by default none), smooth (optional: remove lag outliers from
 * subtracted values, see smoothLag), change (optional: Change mode, see
 * below).
 *
 * A dir less than DEPTH below its root and in data.split becomes an
 * own-files band (key `${dir}/.`: the files directly in it, its value minus
 * its subdirs', at least 0) plus the bands of its stored subdirs, likewise;
 * any other dir is one band of its value (key: the dir). Absent counts as
 * 0. Bands with the same key appear once (ECharts series ids must be
 * unique). That does not make a set holding a dir and its subdir safe: the
 * subdir's bytes are counted in both. Keys do not depend on the metric.
 *
 * HIDDEN holds band keys, and hiding removes the key's bytes at every
 * Depth: a dir key, the dir's subtree; an own-files key `X/.`, the files
 * directly in X. So the bands under a hidden dir key, and a hidden
 * own-files band, are dropped; and a drawn dir band that holds hidden bytes
 * (a dir strictly above a hidden dir, or X or above for `X/.`) has them
 * subtracted, at least 0. Otherwise a hide made at one Depth would leak
 * back in at a shallower one while its chip still says it is hidden. A key
 * under another hidden dir key is skipped, so nothing is subtracted twice;
 * an unknown dir, or `X/.` for an X not in data.split, subtracts nothing.
 * With SMOOTH, both subtractions (own files, hidden bytes) go through
 * smoothLag over the whole history before the clamp at 0, so zooming never
 * changes a value.
 *
 * Of the other bands, those that do not pass MIN and FLAT over the range
 * (see passes), after those subtractions, are filtered out, except those
 * in KEEP. The rest rank by span (max − min) over the range, ties by key,
 * so dirs that shrank or vanished stay visible.
 * TOP of them are returned, in stack order (bottom first): the reverse of
 * rank order, so the least changed lie at the bottom and the movers ride on
 * top of a steady base. Which TOP: those whose keys are
 * in KEEP (optional; the legend's picks) first, so a pick is never cut by
 * zooming or sorting, then the first of the rest in BY's order, ties by
 * rank. BY "" (the default) is rank; "size" keeps the largest at the
 * range's end, "-size" the smallest, "delta" the most grown, "-delta" the
 * most shrunk, "name" the first by label. Returns {bands, cut, filtered}:
 * those bands, as {key, dir, own, label, values, drillable}, how many
 * ranked bands were left out, and how many were filtered out.
 *
 * With CHANGE, the same bands are returned, each with its rates too (see
 * rates), stacked instead by rate span (max − min of its rates in the
 * range, points a+1..b; 0 if none), smallest at the bottom, ties by key:
 * steady growers form the base, bursts lie outside.
 */
export function stackBands(data, opts) {
  const { metric = "rbytes", roots, base, depth, top, hidden, range, keep = [], by = "", min = 0, flat = true } = opts;
  const { smooth = false, change = false } = opts;
  const series = data[metric];
  const zeroed = (dir) => series[dir].map((v) => v ?? 0);
  // VALUES less each of SUBTRACTED, smoothed if asked, at least 0.
  const minus = (values, subtracted) => {
    const diff = values.map((v, i) => subtracted.reduce((rest, s) => rest - s[i], v));
    return (smooth ? smoothLag(diff, subtracted) : diff).map((v) => Math.max(0, v));
  };
  // The files directly in split dir DIR: its value minus its subdirs', at least 0.
  const ownFiles = (dir) => minus(zeroed(dir), data.children.get(dir).map(zeroed));
  const all = new Map(); // band key -> band
  const add = (band) => all.has(band.key) || all.set(band.key, band);
  const expand = (dir, level) => {
    if (level >= depth || !data.split.has(dir)) {
      add({ key: dir, dir, own: false, values: zeroed(dir) });
      return;
    }
    add({ key: `${dir}/.`, dir, own: true, values: ownFiles(dir) });
    for (const kid of data.children.get(dir)) expand(kid, level + 1);
  };
  for (const root of roots) expand(root, 0);

  const dirKeys = hidden.filter((h) => !h.endsWith("/."));
  const isHidden = (band) => hidden.includes(band.key) || dirKeys.some((h) => isUnder(band.dir, h));
  // Hidden bytes to subtract: [dir holding them, values]. Keys are untrusted
  // (from the URL), so only stored dirs and split dirs count.
  const holds = (x, strict) => dirKeys.some((h) => (h !== x || !strict) && isUnder(x, h));
  const removed = [];
  for (const h of new Set(hidden)) {
    if (!h.endsWith("/.")) {
      if (Object.hasOwn(series, h) && !holds(h, true)) removed.push([h, zeroed(h)]);
    } else {
      const x = h.slice(0, -2);
      if (data.split.has(x) && !holds(x, false)) removed.push([x, ownFiles(x)]);
    }
  }
  const visible = [...all.values()]
    .filter((band) => !isHidden(band))
    .map((band) => {
      if (band.own) return band; // holds no subdir's bytes
      const inside = removed.filter(([x]) => isUnder(x, band.dir));
      if (!inside.length) return band;
      return { ...band, values: minus(band.values, inside.map(([, r]) => r)) };
    });

  const [a, b] = range;
  const scored = visible.map((band) => {
    const shown = band.values.slice(a, b + 1);
    const peak = Math.max(...shown);
    return { band, peak, span: peak - Math.min(...shown) };
  });
  const passing = scored.filter((x) => keep.includes(x.band.key) || passes(x, { min, flat }));
  // Labeled before choosing: BY "name" sorts by label.
  const ranked = passing
    .toSorted((x, y) => y.span - x.span || (x.band.key < y.band.key ? -1 : 1))
    .map(({ band }) => ({
      ...band,
      label: bandLabel(data, band, base ?? ROOT, METRICS[metric].own),
      drillable: !band.own && data.split.has(band.dir),
    }));
  const key = by.replace(/^-/, "");
  // toSorted is stable, so ties stay in rank order.
  const order = Object.hasOwn(LEGEND_SORTS, key) ? ranked.toSorted(legendOrder(key, by, range)) : ranked;
  const kept = order.filter((band) => keep.includes(band.key)).slice(0, top);
  const rest = order.filter((band) => !keep.includes(band.key)).slice(0, top - kept.length);
  const chosen = new Set([...kept, ...rest]);
  const bands = ranked.filter((band) => chosen.has(band)).toReversed();
  const result = { cut: ranked.length - bands.length, filtered: scored.length - passing.length };
  if (!change) return { bands, ...result };
  const rated = bands.map((band) => {
    const r = rates(band.values, data.times);
    const shown = r.slice(a + 1, b + 1);
    return { band: { ...band, rates: r }, span: shown.length ? Math.max(...shown) - Math.min(...shown) : 0 };
  });
  rated.sort((x, y) => x.span - y.span || (x.band.key < y.band.key ? -1 : 1));
  return { bands: rated.map(({ band }) => band), ...result };
}

/** BAND's label: its dir relative to BASE, where BASE itself (and ROOT) is
 * named, not "."; an own-files band adds "/ OWN" (see METRICS). */
function bandLabel(data, band, base, own) {
  const { dir } = band;
  let name = relative(dir, base);
  if (dir === ROOT) name = data.tree.fs_path;
  else if (dir === base) name = dir.slice(dir.lastIndexOf("/") + 1);
  return band.own ? `${name}/ ${own}` : name;
}

/** How many levels below ROOTS the Stack view can split: the most levels
 * of data.split below any of them, 0 if none is split. */
export function maxDepth(data, roots) {
  const levels = (dir) =>
    data.split.has(dir) ? 1 + Math.max(0, ...data.children.get(dir).map(levels)) : 0;
  return Math.max(0, ...roots.map(levels));
}

/** The deepest of PATHS that PATH is under (or is), or null if none. */
export function setDirOf(paths, path) {
  let best = null;
  for (const p of paths) {
    if (isUnder(path, p) && (best === null || depth(p) > depth(best))) best = p;
  }
  return best;
}

/** Whether drilling can reach PATH from the set dirs PATHS: PATH is one of
 * them, or a stored subdir of a split dir that drilling can reach. So an
 * untrusted PATH (e.g. from the URL) is safe to look up in a metric's series. */
export function reachable(data, paths, path) {
  if (paths.includes(path)) return true;
  if (path === ROOT) return false;
  const parent = parentOf(path);
  return data.children.get(parent)?.includes(path) === true && reachable(data, paths, parent);
}

/**
 * Shape the set dirs PATHS (a set's, in DATA.sets) into trend cards of
 * METRIC (a METRICS key) for RANGE ([first, last] point indexes), in PATHS
 * order.
 *
 * Returns [{path, values, start, end, change, peak, span, lo, hi, rates,
 * rate}]: values
 * is the dir's whole series (null: absent). Within the range, absent
 * counting as 0 (as in the Stack view): start and end are its values at the ends,
 * change = end - start, peak the largest and span the largest minus the
 * smallest, so a dir that appears or disappears has a span. lo and hi bound
 * its present values (the sparkline's scale), both null if it is absent
 * throughout. With CHANGE (Change mode) a card also has rates, the whole
 * series' rates (see rates), and rate, the average rate over the range:
 * change over its duration (0 for one point).
 */
export function trendCards(data, paths, range, metric = "rbytes", { change = false } = {}) {
  const [a, b] = range;
  const days = (Date.parse(data.times[b]) - Date.parse(data.times[a])) / DAY_MS;
  return paths.map((path) => {
    const values = data[metric][path];
    const inRange = values.slice(a, b + 1);
    const present = inRange.filter((v) => v !== null);
    const zeroed = inRange.map((v) => v ?? 0);
    const start = values[a] ?? 0;
    const end = values[b] ?? 0;
    const peak = Math.max(...zeroed);
    return {
      path,
      values,
      start,
      end,
      change: end - start,
      peak,
      span: peak - Math.min(...zeroed),
      lo: present.length ? Math.min(...present) : null,
      hi: present.length ? Math.max(...present) : null,
      ...(change && { rates: rates(values, data.times), rate: days > 0 ? (end - start) / days : 0 }),
    };
  });
}

/** Change mode's one scale for all of CARDS (from trendCards): {lo, hi},
 * the lowest and highest of their rates in RANGE (points a+1..b), with 0
 * always inside. */
export function rateScale(cards, [a, b]) {
  let [lo, hi] = [0, 0];
  for (const card of cards) {
    for (let i = a + 1; i <= b; i++) [lo, hi] = [Math.min(lo, card.rates[i]), Math.max(hi, card.rates[i])];
  }
  return { lo, hi };
}

/** Whether CARD's dir is unchanged in its range: constant, or absent throughout. */
export const unchanged = (card) => card.span === 0;

/** Whether a band or card X ({peak, span} over the shown range) passes a
 * view's filters: a peak of at least MIN (in the metric's unit), and a
 * change unless FLAT (show unchanged). */
export const passes = (x, { min, flat }) => x.peak >= min && (flat || !unchanged(x));

/** Why a view's filters MIN and FLAT drop things, e.g. "unchanged in this
 * range, or under 1.00 GiB at peak", MIN in FORMAT (default bytes); "" if
 * they drop nothing. */
export function filterReason({ min, flat }, format = formatBytes) {
  const why = [];
  if (!flat) why.push("unchanged in this range");
  if (min > 0) why.push(`under ${format(min)} at peak`);
  return why.join(", or ");
}

// Card orders for the Trends view: sort key -> [label, card's value].
// Values sort largest first; "name" sorts by path.
export const CARD_SORTS = {
  span: ["Span (max − min)", (card) => card.span],
  peak: ["Peak", (card) => card.peak],
  size: ["Current size", (card) => card.end],
  name: ["Name", null],
};

/** CARD's trend over its range, by the sign of its change: "grow",
 * "shrink" or "flat" (ends where it started, or absent throughout). */
export const trend = (card) => (card.change > 0 ? "grow" : card.change < 0 ? "shrink" : "flat");

// The Trends view's card groups, in order: every sort keeps them apart.
const TREND_ORDER = ["grow", "shrink", "flat"];

/** CARDS grouped by trend (growing, shrinking, then flat), each group
 * ordered by sort key BY (see CARD_SORTS); ties by path. Paths compare by
 * UTF-16 code unit, i.e. by byte for ASCII names. */
export function sortCards(cards, by) {
  const byPath = (x, y) => (x.path < y.path ? -1 : x.path > y.path ? 1 : 0);
  const byGroup = (x, y) => TREND_ORDER.indexOf(trend(x)) - TREND_ORDER.indexOf(trend(y));
  const key = CARD_SORTS[by][1];
  const within = key ? (x, y) => key(y) - key(x) || byPath(x, y) : byPath;
  return cards.toSorted((x, y) => byGroup(x, y) || within(x, y));
}

// Legend columns: sort key -> a band's value over range [a, b]. Values sort
// largest first; "name" sorts by label, A to Z.
export const LEGEND_SORTS = {
  name: null,
  size: (band, [, b]) => band.values[b],
  delta: (band, [a, b]) => band.values[b] - band.values[a],
};

/**
 * BANDS (from stackBands, bottom first) in legend order for sort BY over
 * RANGE: "" is chart order, the top of the stack first; a LEGEND_SORTS key
 * is that column's order, and "-" before it reverses it. Ties keep chart
 * order. Labels compare by UTF-16 code unit. Bands in FIRST (optional; the
 * ticked ones) come before the rest, each group in that order.
 */
export function sortLegend(bands, by, range, first = bands) {
  const top = bands.toReversed();
  const key = by.replace(/^-/, "");
  const sorted = Object.hasOwn(LEGEND_SORTS, key) ? top.toSorted(legendOrder(key, by, range)) : top;
  return [...sorted.filter((band) => first.includes(band)), ...sorted.filter((band) => !first.includes(band))];
}

/** The comparator of legend column KEY, reversed if BY starts with "-". */
function legendOrder(key, by, range) {
  const value = LEGEND_SORTS[key];
  const cmp = value
    ? (x, y) => value(y, range) - value(x, range)
    : (x, y) => (x.label < y.label ? -1 : x.label > y.label ? 1 : 0);
  const sign = by.startsWith("-") ? -1 : 1;
  return (x, y) => sign * cmp(x, y);
}

/** The point whose rate's interval (t[i-1], t[i]] holds time MS, of TIMES
 * (ISO strings): the first at or after MS, at least 1; the last if MS is
 * past it. Change mode's steps, tooltip and clicks use it. */
export function stepIndex(times, ms) {
  const next = msOf(times).findIndex((x) => x >= ms);
  return next < 0 ? times.length - 1 : Math.max(1, next);
}

/**
 * Index of the layer (bottom first) whose stacked span at time MS holds
 * VALUE, or -1. Layers are linear between TIMES, as the chart draws them,
 * and flat beyond the first and last points.
 *
 * With STEP (Change mode), layers are steps instead: at MS, each has point
 * i's value, i the first point at or after MS (its interval holds MS), and
 * values stack by sign as ECharts' samesign stacking draws them: those
 * >= 0 up from 0, those < 0 down from 0, each in layer order.
 */
export function layerAt(layers, times, ms, value, { step = false } = {}) {
  const t = msOf(times);
  if (step) {
    const i = stepIndex(times, ms);
    const edge = [0, 0]; // the positive and negative stacks' outer edges
    for (const [k, values] of layers.entries()) {
      const v = values[i] ?? 0;
      const side = v >= 0 ? 0 : 1;
      const from = edge[side];
      edge[side] += v;
      if (Math.min(from, edge[side]) <= value && value <= Math.max(from, edge[side])) return k;
    }
    return -1;
  }
  const i = Math.max(0, t.findLastIndex((x) => x <= ms));
  const j = Math.min(i + 1, t.length - 1);
  const f = t[j] > t[i] ? Math.min(1, Math.max(0, (ms - t[i]) / (t[j] - t[i]))) : 0;
  let top = 0;
  for (const [k, values] of layers.entries()) {
    top += values[i] + f * (values[j] - values[i]);
    if (value >= 0 && value <= top) return k;
  }
  return -1;
}

/** [first point index at or after FROMMS, last at or before TOMS], at least one point. */
export function indexRange(times, fromMs, toMs) {
  const ms = times.map((t) => Date.parse(t));
  let a = ms.findIndex((m) => m >= fromMs);
  let b = ms.findLastIndex((m) => m <= toMs);
  if (a < 0) a = ms.length - 1;
  if (b < a) b = a;
  return [a, b];
}
