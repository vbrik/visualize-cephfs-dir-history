// View 2b: a watched dir's trend card sparkline (ECharts).

import { escapeHtml, formatBytes } from "../model.js";

/**
 * Return the ECharts option for CARD (from model.trendCards) on DATA's
 * times, over RANGE ([a, b] point indexes) only.
 *
 * colors: {grow, growFill, shrink, shrinkFill, flat, flatFill, surface, ink,
 * grid}. The line takes the color of the change's direction. The y-scale
 * fits the card's values in the range; a constant series sits mid-height.
 */
export function trendCardOption(data, card, { colors, range }) {
  const [a, b] = range;
  const [line, fill] =
    card.change > 0
      ? [colors.grow, colors.growFill]
      : card.change < 0
        ? [colors.shrink, colors.shrinkFill]
        : [colors.flat, colors.flatFill];
  const flat = card.lo !== null && card.lo === card.hi;
  const points = data.times.slice(a, b + 1).map((t, i) => [Date.parse(t), card.values[a + i]]);
  return {
    animation: false,
    grid: { left: 0, right: 0, top: 3, bottom: 3 },
    xAxis: { type: "time", show: false, min: points[0][0], max: points.at(-1)[0] },
    yAxis: {
      type: "value",
      show: false,
      min: card.lo === null ? 0 : flat ? card.lo - 1 : card.lo,
      max: card.hi === null ? 1 : flat ? card.hi + 1 : card.hi,
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "line", lineStyle: { color: colors.grid, width: 1 } },
      backgroundColor: colors.surface,
      borderColor: colors.grid,
      textStyle: { color: colors.ink },
      confine: true,
      formatter: ([p]) => {
        const v = p.value[1];
        return `<div class="tt"><div class="tt-when">${escapeHtml(new Date(p.value[0]).toLocaleString())}</div>`
          + `<b>${v === null ? "absent" : formatBytes(v)}</b></div>`;
      },
    },
    series: [
      {
        type: "line",
        symbol: "none",
        connectNulls: false,
        data: points,
        lineStyle: { width: 2, color: line },
        itemStyle: { color: line },
        // Fill down to the axis bottom (the card's lo), not to 0.
        areaStyle: { color: fill, opacity: 1, origin: "start" },
        emphasis: { disabled: true },
      },
    ],
  };
}
