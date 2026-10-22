// Pure data shaping for the charts; no DOM. See docs/terms.md for "unit"
// and "own size". A tree file holds, per time point, each unit's own size
// and the rbytes of ROOT, the units and their ancestors.

export const ROOT = ".";

// Distinct bands a chart may color: the categorical palette's slot count.
export const MAX_BANDS = 8;

/** Number of path components; ROOT is 0. */
export function depth(path) {
  return path === ROOT ? 0 : path.split("/").length;
}

/** Whether PATH is BASE or inside it. */
export function isUnder(path, base) {
  return base === ROOT || path === base || path.startsWith(`${base}/`);
}

/** PATH's ancestor at depth D, or PATH itself if it is not deeper. */
export function ancestorAt(path, d) {
  return depth(path) <= d ? path : path.split("/").slice(0, d).join("/");
}

/** PATH relative to BASE ("." for BASE itself). */
export function relative(path, base) {
  if (path === base) return ".";
  return base === ROOT ? path : path.slice(base.length + 1);
}

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

/** Signed change, e.g. "+3.1 GiB". */
export function formatDelta(bytes) {
  return bytes > 0 ? `+${formatBytes(bytes)}` : bytes < 0 ? formatBytes(bytes) : "±0 B";
}

/** Bytes in TEXT ("1024", "10GiB", "1.5 tib"), or null if it is not a size. */
export function parseSize(text) {
  const m = /^\s*(\d+(?:\.\d+)?)\s*(?:([kmgtp])ib)?\s*$/i.exec(text);
  if (!m || (!m[2] && m[1].includes("."))) return null;
  const power = m[2] ? "kmgtp".indexOf(m[2].toLowerCase()) + 1 : 0;
  return Math.floor(Number(m[1]) * 1024 ** power);
}

/**
 * Shape a tree's units into stacked bands.
 *
 * opts: inPath (drilled-into dir), group (levels below inPath to sum units
 * into; 0 = each unit is a band), top (max named bands), min (bytes: a band
 * whose peak over the range is lower folds into "smaller"), hidden (paths
 * whose units are dropped entirely), range ([first, last] point indexes).
 *
 * Bands are ranked by peak over the range, ties by key, so dirs that shrank
 * or vanished stay visible. Returns {bands, smaller, rest}: bands are
 * {key, label, units, values, drillable}; smaller sums the folded units;
 * rest is inPath's bytes in no unit (rbytes minus all its units' own sizes,
 * at least 0). Hidden units are in neither, so hiding shrinks the total.
 */
export function stack(data, opts) {
  const { inPath, group, top, min, hidden, range } = opts;
  const n = data.times.length;
  const [a, b] = range;
  const all = Object.keys(data.units).filter((u) => isUnder(u, inPath));
  const isHidden = (u) => hidden.some((h) => isUnder(u, h));

  const groups = new Map();
  for (const u of all) {
    if (isHidden(u)) continue;
    const key = group ? ancestorAt(u, depth(inPath) + group) : u;
    if (!groups.has(key)) groups.set(key, { key, units: [], values: new Array(n).fill(0) });
    const g = groups.get(key);
    g.units.push(u);
    data.units[u].own.forEach((v, i) => {
      g.values[i] += v;
    });
  }

  const peak = (g) => Math.max(...g.values.slice(a, b + 1));
  const ranked = [...groups.values()]
    .map((g) => ({ ...g, peak: peak(g) }))
    .sort((x, y) => y.peak - x.peak || (x.key < y.key ? -1 : 1));
  const bands = ranked.filter((g) => g.peak >= min).slice(0, Math.min(top, MAX_BANDS));
  const shown = new Set(bands.map((g) => g.key));

  const smaller = new Array(n).fill(0);
  for (const g of ranked) {
    if (!shown.has(g.key)) g.values.forEach((v, i) => (smaller[i] += v));
  }
  const base = data.rbytes[inPath] ?? new Array(n).fill(null);
  const rest = base.map((r, i) =>
    Math.max(0, (r ?? 0) - all.reduce((s, u) => s + data.units[u].own[i], 0)),
  );

  return {
    bands: bands.map((g) => ({
      key: g.key,
      units: g.units,
      values: g.values,
      label: bandLabel(g, data, inPath),
      drillable: g.units.some((u) => u !== g.key),
    })),
    smaller,
    rest,
  };
}

function bandLabel(g, data, inPath) {
  const rel = relative(g.key, inPath);
  if (g.units.length === 1 && g.units[0] === g.key) {
    const name = rel === "." ? "this directory" : rel;
    return data.units[g.key].leaf ? name : `${name} (own files)`;
  }
  const n = g.units.length;
  return `${rel}/ (${n} unit${n === 1 ? "" : "s"})`;
}

/** Index of the time point nearest to MS. */
export function nearestIndex(times, ms) {
  let best = 0;
  times.forEach((t, i) => {
    if (Math.abs(Date.parse(t) - ms) < Math.abs(Date.parse(times[best]) - ms)) best = i;
  });
  return best;
}

/** Index of the layer (bottom first) whose stacked span at point I holds VALUE, or -1. */
export function layerAt(layers, i, value) {
  let top = 0;
  for (const [k, values] of layers.entries()) {
    top += values[i];
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
