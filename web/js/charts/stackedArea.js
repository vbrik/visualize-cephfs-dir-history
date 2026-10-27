// The Stack view's chart: stacked area of its bands over time (ECharts).

import { escapeHtml, formatBytes, formatDelta } from "../model.js";

/**
 * Return the ECharts option for MODEL (from model.stackBands) on DATA's
 * times; the first band is the bottom layer.
 *
 * colors: {bands: Map key -> color, surface, ink, muted, grid}. range: [a, b]
 * point indexes; the tooltip shows each value's change since point a.
 */
export function stackedAreaOption(data, model, { colors, range }) {
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
      axisLabel: { color: colors.muted, formatter: (v) => formatBytes(v) },
      splitLine: { lineStyle: { color: colors.grid, width: 1 } },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: colors.muted, width: 1 } },
      backgroundColor: colors.surface,
      borderColor: colors.grid,
      textStyle: { color: colors.ink },
      confine: true,
      // params is empty when no band is drawn (all hidden).
      formatter: (params) => (params.length ? tooltip(params[0].dataIndex, layers, data, range) : ""),
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

function tooltip(i, layers, data, [a]) {
  const when = new Date(data.times[i]).toLocaleString();
  const rows = [...layers]
    .reverse()
    .map((l) => row(l.color, l.name, l.values[i], l.values[i] - l.values[a]));
  const drawn = layers.reduce((s, l) => s + l.values[i], 0);
  const drawnStart = layers.reduce((s, l) => s + l.values[a], 0);
  rows.push(row(null, "Total shown", drawn, drawn - drawnStart));
  return `<div class="tt"><div class="tt-when">${escapeHtml(when)}</div>${rows.join("")}`
    + `<div class="tt-note">change since ${escapeHtml(new Date(data.times[a]).toLocaleString())}</div></div>`;
}

function row(color, name, value, delta) {
  const key = color ? `<span class="tt-key" style="background:${color}"></span>` : `<span class="tt-key"></span>`;
  return `<div class="tt-row">${key}<b>${formatBytes(value)}</b>`
    + `<span class="tt-delta">${formatDelta(delta)}</span><span class="tt-name">${escapeHtml(name)}</span></div>`;
}
