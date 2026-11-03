// The Stack view's chart: stacked area of its bands over time (ECharts).

import { escapeHtml, formatBytes, formatDelta, formatRate, stepIndex } from "../model.js";

/**
 * Return the ECharts option for MODEL ({bands}: those of model.stackBands
 * to draw) on DATA's times; the first band is the bottom layer.
 *
 * colors: {bands: Map key -> color, surface, ink, muted, grid}. range: [a, b]
 * point indexes; the tooltip shows each value's change since point a.
 * format: a value as text (default model.formatBytes), for the axis and tooltip.
 * order: MODEL's bands in the tooltip's row order (default: stack order, top
 * first), e.g. the legend's, so each row's place does not depend on the
 * hovered point.
 * gaps: Map key -> a band's gap line width (px), e.g. as gapWidths last fit
 * it; GAP_PX for a band not in it.
 * change: Change mode. The bands' rates (model.stackBands with change; null
 * as 0) are drawn instead of their values, as step areas (ECharts step
 * "start": point i's rate fills its interval (t[i-1], t[i]]), stacked by
 * sign (samesign: up from 0 and down from 0), with no gaps; the tooltip
 * shows the rates of the step under the pointer and their net.
 */
export function stackedAreaOption(data, model, { colors, range, format = formatBytes, order, gaps = new Map(), change = false }) {
  const times = data.times.map((t) => Date.parse(t));
  const layers = model.bands.map((b) => ({
    id: b.key,
    name: b.label,
    values: change ? b.rates.map((v) => v ?? 0) : b.values,
    color: colors.bands.get(b.key),
    gap: change ? 0 : (gaps.get(b.key) ?? GAP_PX),
  }));
  const layerOf = new Map(layers.map((l) => [l.id, l]));
  const rowOrder = order ? order.map((b) => layerOf.get(b.key)) : layers.toReversed();

  return {
    animation: false,
    textStyle: { fontFamily: "system-ui, -apple-system, 'Segoe UI', sans-serif" },
    grid: { left: 72, right: 24, top: 16, bottom: 72 },
    xAxis: {
      type: "time",
      axisLine: { lineStyle: { color: colors.grid } },
      axisTick: { show: false },
      axisLabel: { color: colors.muted, hideOverlap: true },
      splitLine: { show: false },
    },
    yAxis: {
      type: "value",
      ...(change ? {} : { min: 0 }),
      axisLabel: { color: colors.muted, formatter: (v) => (change ? formatRate(v, format) : format(v)) },
      splitLine: { lineStyle: { color: colors.grid, width: 1 } },
    },
    tooltip: {
      trigger: "axis",
      // Change mode: the step under the pointer, not the nearest point, so
      // the pointer does not snap and the tooltip finds the step itself.
      axisPointer: { type: "line", snap: !change, lineStyle: { color: colors.muted, width: 1 } },
      backgroundColor: colors.surface,
      borderColor: colors.grid,
      textStyle: { color: colors.ink },
      confine: true,
      // params is empty when no band is drawn (all hidden or unticked).
      formatter: (params) => {
        if (!params.length) return "";
        if (change) return rateTooltip(stepIndex(data.times, params[0].axisValue), layers, rowOrder, data, format);
        return tooltip(params[0].dataIndex, layers, rowOrder, data, range, format);
      },
    },
    dataZoom: [
      { type: "slider", bottom: 16, height: 24, labelFormatter: "", borderColor: colors.grid },
      { type: "inside" },
    ],
    series: layers.map((l) => ({
      id: l.id,
      name: l.name,
      type: "line",
      stack: "total",
      symbol: "none",
      ...(change ? { step: "start" } : {}),
      data: times.map((t, i) => [t, l.values[i]]),
      // The line in the surface color is the gap between stacked bands.
      lineStyle: { width: l.gap, color: colors.surface },
      itemStyle: { color: l.color },
      areaStyle: { color: l.color, opacity: 0.9 },
      emphasis: { disabled: true },
    })),
  };
}

// Width (px) of the line on a band's top edge that is the gap between it and
// the band above. Centered on the edge, it covers GAP_PX / 2 of both.
export const GAP_PX = 2;
// Height (px) both bands at an edge need for it to keep its gap: a band with
// gaps on both edges shows MIN_GAPPED_PX - GAP_PX of its color.
export const MIN_GAPPED_PX = 4;

/**
 * The gap line width (px) of each of BANDS (stack order, bottom first) over
 * RANGE ([a, b] point indexes) at PXPERUNIT pixels per value unit. An edge
 * keeps its gap only if the bands on both sides of it are at least
 * MIN_GAPPED_PX tall wherever they are present (above 0) in RANGE, so the
 * gaps never hide a thin band. The top band's edge has nothing above it. A
 * band absent from all of RANGE has no gap, and the bands below and above
 * it meet at its edge.
 */
export function gapWidths(bands, [a, b], pxPerUnit) {
  const widths = Array(bands.length).fill(0);
  let above = Infinity; // height (px) of the nearest present band above
  for (let j = bands.length - 1; j >= 0; j--) {
    let min = Infinity;
    for (let i = a; i <= b; i++) {
      const v = bands[j].values[i];
      if (v > 0 && v < min) min = v;
    }
    if (min === Infinity) continue;
    const height = min * pxPerUnit;
    if (height >= MIN_GAPPED_PX && above >= MIN_GAPPED_PX) widths[j] = GAP_PX;
    above = height;
  }
  return widths;
}

// Most bands the tooltip lists; the rest share one row.
const TOOLTIP_ROWS = 30;

/** The tooltip at point I: the largest LAYERS there, in ROWORDER (the
 * same layers, reordered); the rest summed in one row; and the total. */
function tooltip(i, layers, rowOrder, data, [a], format) {
  const when = new Date(data.times[i]).toLocaleString();
  const listed = new Set(layers.toSorted((x, y) => y.values[i] - x.values[i]).slice(0, TOOLTIP_ROWS));
  const sum = (ls, j) => ls.reduce((s, l) => s + l.values[j], 0);
  const rows = rowOrder
    .filter((l) => listed.has(l))
    .map((l) => row(l.color, l.name, l.values[i], l.values[i] - l.values[a], format));
  const others = layers.filter((l) => !listed.has(l));
  if (others.length) {
    const value = sum(others, i);
    rows.push(row(null, `${others.length} smaller bands`, value, value - sum(others, a), format));
  }
  const drawn = sum(layers, i);
  rows.push(row(null, "Total shown", drawn, drawn - sum(layers, a), format));
  return `<div class="tt"><div class="tt-when">${escapeHtml(when)}</div>${rows.join("")}`
    + `<div class="tt-note">change since ${escapeHtml(new Date(data.times[a]).toLocaleString())}</div></div>`;
}

/** Change mode's tooltip at point I: the rates of the largest (by size of
 * rate) LAYERS there, in ROWORDER; the rest summed in one row; and the net
 * rate of all. */
function rateTooltip(i, layers, rowOrder, data, format) {
  const when = `${new Date(data.times[i - 1]).toLocaleString()} – ${new Date(data.times[i]).toLocaleString()}`;
  const rate = (value) => formatRate(value, format);
  const by = (l) => Math.abs(l.values[i]);
  const listed = new Set(layers.toSorted((x, y) => by(y) - by(x)).slice(0, TOOLTIP_ROWS));
  const sum = (ls) => ls.reduce((s, l) => s + l.values[i], 0);
  const rows = rowOrder.filter((l) => listed.has(l)).map((l) => cells(l.color, l.name, rate(l.values[i])));
  const others = layers.filter((l) => !listed.has(l));
  if (others.length) rows.push(cells(null, `${others.length} smaller bands`, rate(sum(others))));
  rows.push(cells(null, "Net", rate(sum(layers))));
  return `<div class="tt"><div class="tt-when">${escapeHtml(when)}</div>${rows.join("")}</div>`;
}

function row(color, name, value, delta, format) {
  return cells(color, name, format(value), formatDelta(delta, format));
}

/** A tooltip row: a color key (none if COLOR is null), MAIN in bold, EXTRA
 * (optional) and NAME. MAIN and EXTRA are formatted numbers, not names. */
function cells(color, name, main, extra = "") {
  const key = color ? `<span class="tt-key" style="background:${color}"></span>` : `<span class="tt-key"></span>`;
  const more = extra ? `<span class="tt-delta">${extra}</span>` : "";
  return `<div class="tt-row">${key}<b>${main}</b>${more}<span class="tt-name">${escapeHtml(name)}</span></div>`;
}
