// Pure data shaping for the charts; no DOM. See docs/terms.md for "unit"
// and "own size". A tree file holds, per time point, each unit's own size
// and the rbytes of ROOT, the units, the watched dirs and their ancestors.

export const ROOT = ".";

// Distinct bands a chart may color: the categorical palette's slot count.
export const MAX_BANDS = 8;

/** The hidden-list entry that hides BAND: its unit, or, for a group, every
 * unit below the group's dir (paths never end in "/", so "dir/" is unique). */
export function hideKey(band) {
  return band.drillable ? `${band.key}/` : band.key;
}

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
 * whose peak over the range is lower folds into "smaller"), hidden (units
 * dropped entirely: a unit path, or "dir/" for every unit in dir's
 * subtree; see hideKey), range ([first, last] point indexes).
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
  const isHidden = (u) =>
    hidden.some((h) => (h.endsWith("/") ? isUnder(u, h.slice(0, -1)) : u === h));

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

/**
 * Shape DATA's watched dirs into trend cards for RANGE ([first, last] point
 * indexes), in DATA.watched order.
 *
 * Returns [{path, values, start, end, change, lo, hi}]: values is the dir's
 * whole rbytes series (null: absent); start and end its bytes at the range's
 * ends, absent counting as 0 (as for units), and change = end - start; lo
 * and hi bound its values within the range, both null if it is absent
 * throughout. A file without watched dirs gives [].
 */
export function trendCards(data, range) {
  const [a, b] = range;
  return (data.watched ?? []).map((path) => {
    const values = data.rbytes[path];
    const shown = values.slice(a, b + 1).filter((v) => v !== null);
    const start = values[a] ?? 0;
    const end = values[b] ?? 0;
    return {
      path,
      values,
      start,
      end,
      change: end - start,
      lo: shown.length ? Math.min(...shown) : null,
      hi: shown.length ? Math.max(...shown) : null,
    };
  });
}

/**
 * Index of the layer (bottom first) whose stacked span at time MS holds
 * VALUE, or -1. Layers are linear between TIMES, as the chart draws them,
 * and flat beyond the first and last points.
 */
export function layerAt(layers, times, ms, value) {
  const t = times.map((x) => Date.parse(x));
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
