// Pure data shaping for the charts; no DOM. See docs/terms.md for "set",
// "stored dir" and "own-files band". A tree file holds, per time point, the
// rbytes of every stored dir (a set dir, or one within its depth below it),
// the tree's sets, and `split`: the stored dirs whose subdirs are all stored.

export const ROOT = ".";

// Most bands the Stack view draws (Top's max): the categorical palette's
// slot count, so colors never repeat.
export const MAX_BANDS = 64;

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

/** Signed change, e.g. "+3.1 GiB". */
export function formatDelta(bytes) {
  return bytes > 0 ? `+${formatBytes(bytes)}` : bytes < 0 ? formatBytes(bytes) : "±0 B";
}

/**
 * RAW, a tree file as read, ready for the views: every rbytes series a list
 * of one value (bytes, or null: absent) per time point, where the file has
 * one number for a series that never changed; `split` a Set; and `children`
 * a Map from each split dir to its stored subdirs, sorted.
 */
export function normalize(raw) {
  const n = raw.times.length;
  const rbytes = Object.fromEntries(
    Object.entries(raw.rbytes).map(([p, v]) => [p, Array.isArray(v) ? v : new Array(n).fill(v)]),
  );
  const split = new Set(raw.split);
  const children = new Map([...split].map((dir) => [dir, []]));
  for (const p of Object.keys(rbytes)) {
    if (p !== ROOT) children.get(parentOf(p))?.push(p);
  }
  for (const kids of children.values()) kids.sort();
  return { ...raw, rbytes, split, children };
}

/**
 * The Stack view's bands for DATA (from normalize).
 *
 * opts: roots (the dirs to show: a set's dirs, or the drilled-into dir),
 * base (labels are relative to it; null: the tree root), depth (levels to
 * split below each root), top (most bands returned), hidden (band keys; see
 * below), range ([first, last] point indexes).
 *
 * A dir less than DEPTH below its root and in data.split becomes an
 * own-files band (key `${dir}/.`: the files directly in it, its rbytes minus
 * its subdirs', at least 0) plus the bands of its stored subdirs, likewise;
 * any other dir is one band of its rbytes (key: the dir). Absent counts as
 * 0. A band reached from two roots (a set holding a dir and its subdir)
 * appears once.
 *
 * A band is hidden if HIDDEN holds its key, or a dir key (not ending "/.")
 * that its dir is under: hiding a dir hides its subtree at any depth,
 * hiding an own-files band only that band. The other bands rank by span
 * (max − min) over the range, ties by key, so dirs that shrank or vanished
 * stay visible. Returns {bands, cut}: the first TOP, as {key, dir, own,
 * label, values, drillable}, and how many ranked past them.
 */
export function stackBands(data, { roots, base, depth, top, hidden, range }) {
  const zeroed = (dir) => data.rbytes[dir].map((v) => v ?? 0);
  const all = new Map(); // band key -> band
  const add = (band) => all.has(band.key) || all.set(band.key, band);
  const expand = (dir, level) => {
    if (level >= depth || !data.split.has(dir)) {
      add({ key: dir, dir, own: false, values: zeroed(dir) });
      return;
    }
    const kids = data.children.get(dir);
    const own = zeroed(dir);
    for (const kid of kids) data.rbytes[kid].forEach((v, i) => (own[i] -= v ?? 0));
    add({ key: `${dir}/.`, dir, own: true, values: own.map((v) => Math.max(0, v)) });
    for (const kid of kids) expand(kid, level + 1);
  };
  for (const root of roots) expand(root, 0);

  const dirKeys = hidden.filter((h) => !h.endsWith("/."));
  const isHidden = (band) => hidden.includes(band.key) || dirKeys.some((h) => isUnder(band.dir, h));
  const [a, b] = range;
  const span = (values) => {
    const shown = values.slice(a, b + 1);
    return Math.max(...shown) - Math.min(...shown);
  };
  const ranked = [...all.values()]
    .filter((band) => !isHidden(band))
    .map((band) => ({ band, span: span(band.values) }))
    .sort((x, y) => y.span - x.span || (x.band.key < y.band.key ? -1 : 1))
    .map(({ band }) => band);
  const bands = ranked.slice(0, top).map((band) => ({
    ...band,
    label: bandLabel(data, band, base ?? ROOT),
    drillable: !band.own && data.split.has(band.dir),
  }));
  return { bands, cut: ranked.length - bands.length };
}

/** BAND's label: its dir relative to BASE, where BASE itself (and ROOT) is
 * named, not "."; an own-files band adds "/ bytes". */
function bandLabel(data, band, base) {
  const { dir } = band;
  let name = relative(dir, base);
  if (dir === ROOT) name = data.tree.fs_path;
  else if (dir === base) name = dir.slice(dir.lastIndexOf("/") + 1);
  return band.own ? `${name}/ bytes` : name;
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
 * untrusted PATH (e.g. from the URL) is safe to look up in data.rbytes. */
export function reachable(data, paths, path) {
  if (paths.includes(path)) return true;
  if (path === ROOT) return false;
  const parent = parentOf(path);
  return data.children.get(parent)?.includes(path) === true && reachable(data, paths, parent);
}

/**
 * Shape the set dirs PATHS (a set's, in DATA.sets) into trend cards for
 * RANGE ([first, last] point indexes), in PATHS order.
 *
 * Returns [{path, values, start, end, change, peak, span, lo, hi}]: values
 * is the dir's whole rbytes series (null: absent). Within the range, absent
 * counting as 0 (as in the Stack view): start and end are its bytes at the ends,
 * change = end - start, peak the largest and span the largest minus the
 * smallest, so a dir that appears or disappears has a span. lo and hi bound
 * its present values (the sparkline's scale), both null if it is absent
 * throughout.
 */
export function trendCards(data, paths, range) {
  const [a, b] = range;
  return paths.map((path) => {
    const values = data.rbytes[path];
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
    };
  });
}

/** Whether CARD's dir is unchanged in its range: constant, or absent throughout. */
export const unchanged = (card) => card.span === 0;

// Card orders for the Trends view: sort key -> [label, card's value].
// Values sort largest first; "name" sorts by path.
export const CARD_SORTS = {
  span: ["Span (max − min)", (card) => card.span],
  peak: ["Peak", (card) => card.peak],
  size: ["Current size", (card) => card.end],
  name: ["Name", null],
};

/** CARDS ordered by sort key BY (see CARD_SORTS); ties by path. Paths
 * compare by UTF-16 code unit, i.e. by byte for ASCII names. */
export function sortCards(cards, by) {
  const byPath = (x, y) => (x.path < y.path ? -1 : x.path > y.path ? 1 : 0);
  const key = CARD_SORTS[by][1];
  return cards.toSorted(key ? (x, y) => key(y) - key(x) || byPath(x, y) : byPath);
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
