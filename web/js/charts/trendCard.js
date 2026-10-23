// View 2b: a watched dir's trend card sparkline, as plain SVG path data.
// One ECharts instance per card took 20 s to draw 3000 cards; paths drawn
// in a fixed viewBox, stretched to the card (preserveAspectRatio="none",
// non-scaling strokes), cost next to nothing and need no resize handling.

// The viewBox: WIDTH is arbitrary (stretched); HEIGHT and PAD are pixels,
// matching the .spark box's CSS height.
export const WIDTH = 1000;
export const HEIGHT = 56;
const PAD = 3;

/** The viewBox x of each point of TIMES in RANGE ([a, b] point indexes),
 * proportional to time: the first argument of sparkPaths and sparkPointAt,
 * shared by every card. */
export function sparkXs(times, range) {
  const ms = times.slice(range[0], range[1] + 1).map((t) => Date.parse(t));
  const [t0, t1] = [ms[0], ms.at(-1)];
  return ms.map((t) => (t1 > t0 ? ((t - t0) / (t1 - t0)) * WIDTH : WIDTH / 2));
}

/**
 * Return {line, fill}: SVG path data of CARD's (from model.trendCards)
 * sparkline over RANGE, at XS (from sparkXs).
 *
 * The y-scale fits the card's present values in the range (lo..hi); a
 * constant series sits mid-height. The fill reaches down to the scale's
 * bottom, not to 0. Absent points are gaps; a lone point between gaps
 * draws nothing. Both are "" when the dir is absent throughout.
 */
export function sparkPaths(xs, card, range) {
  const [a, b] = range;
  const { lo, hi } = card;
  const bottom = HEIGHT - PAD;
  const y = (v) => (hi === lo ? HEIGHT / 2 : bottom - ((v - lo) / (hi - lo)) * (bottom - PAD));
  const segments = [];
  let run = [];
  for (let i = a; i <= b; i++) {
    const v = card.values[i];
    if (v === null) {
      run = [];
      continue;
    }
    if (!run.length) segments.push(run);
    run.push([xs[i - a], y(v)].map((n) => Math.round(n * 10) / 10));
  }
  let line = "";
  let fill = "";
  for (const s of segments.filter((s) => s.length > 1)) {
    const pts = s.map(([px, py]) => `${px} ${py}`).join(" L ");
    line += `M ${pts} `;
    fill += `M ${s[0][0]} ${bottom} L ${pts} L ${s.at(-1)[0]} ${bottom} Z `;
  }
  return { line: line.trim(), fill: fill.trim() };
}

/** The index of the point in RANGE nearest in time to FRACTION (0..1) of
 * the sparkline's width, and its x: [index, x]. XS is from sparkXs. */
export function sparkPointAt(xs, range, fraction) {
  const want = fraction * WIDTH;
  let best = 0;
  for (const [i, x] of xs.entries()) if (Math.abs(x - want) < Math.abs(xs[best] - want)) best = i;
  return [range[0] + best, xs[best]];
}
