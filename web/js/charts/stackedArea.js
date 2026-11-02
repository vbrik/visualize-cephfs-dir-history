// The Stack view's chart: stacked area of its bands over time (ECharts).

import { escapeHtml, formatBytes, formatDelta } from "../model.js";

/**
 * Return the ECharts option for MODEL ({bands}: those of model.stackBands
 * to draw) on DATA's times; the first band is the bottom layer.
 *
 * colors: {bands: Map key -> color, surface, ink, muted, grid}. range: [a, b]
 * point indexes; the tooltip shows each value's change since point a.
 * format: a value as text (default model.formatBytes), for the axis and tooltip.
 */
export function stackedAreaOption(data, model, { colors, range, format = formatBytes }) {
  const times = data.times.map((t) => Date.parse(t));
  const layers = model.bands.map((b) => ({
    id: b.key,
    name: b.label,
    values: b.values,
    color: colors.bands.get(b.key),
  }));

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
      min: 0,
      axisLabel: { color: colors.muted, formatter: (v) => format(v) },
      splitLine: { lineStyle: { color: colors.grid, width: 1 } },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: colors.muted, width: 1 } },
      backgroundColor: colors.surface,
      borderColor: colors.grid,
      textStyle: { color: colors.ink },
      confine: true,
      // params is empty when no band is drawn (all hidden or unticked).
      formatter: (params) => (params.length ? tooltip(params[0].dataIndex, layers, data, range, format) : ""),
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
      data: times.map((t, i) => [t, l.values[i]]),
      // The line in the surface color is the 2px gap between stacked bands.
      lineStyle: { width: 2, color: colors.surface },
      itemStyle: { color: l.color },
      areaStyle: { color: l.color, opacity: 0.9 },
      emphasis: { disabled: true },
    })),
  };
}

// Most bands the tooltip lists; the rest share one row.
const TOOLTIP_ROWS = 30;

function tooltip(i, layers, data, [a], format) {
  const when = new Date(data.times[i]).toLocaleString();
  // The largest at point I, in stack order (top first).
  const listed = new Set(layers.toSorted((x, y) => y.values[i] - x.values[i]).slice(0, TOOLTIP_ROWS));
  const sum = (ls, j) => ls.reduce((s, l) => s + l.values[j], 0);
  const rows = layers
    .toReversed()
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

function row(color, name, value, delta, format) {
  const key = color ? `<span class="tt-key" style="background:${color}"></span>` : `<span class="tt-key"></span>`;
  return `<div class="tt-row">${key}<b>${format(value)}</b>`
    + `<span class="tt-delta">${formatDelta(delta, format)}</span><span class="tt-name">${escapeHtml(name)}</span></div>`;
}
