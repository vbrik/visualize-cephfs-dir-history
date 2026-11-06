// The Trends view's time-range slider: an ECharts dataZoom on its own,
// with a series of the caller's (the dirset's total) as its data shadow. It applies a drag on release
// (realtime: false): cards are sorted and filtered by the range, so they
// would reorder and come and go under the pointer while it moves.

/** Return the ECharts option of a bare zoom slider over DATA's times.
 * colors: {grid}. shadow: one value per time point. */
export function rangeSliderOption(data, { colors, shadow }) {
  return {
    animation: false,
    grid: { left: 24, right: 24, top: 0, height: 1 },
    xAxis: { type: "time", show: false },
    yAxis: { type: "value", show: false },
    dataZoom: [
      {
        type: "slider",
        realtime: false,
        left: 24,
        right: 24,
        bottom: 12,
        height: 24,
        labelFormatter: "",
        borderColor: colors.grid,
      },
    ],
    series: [
      {
        id: "\0shadow",
        type: "line",
        symbol: "none",
        silent: true,
        lineStyle: { opacity: 0 },
        data: data.times.map((t, i) => [Date.parse(t), shadow[i]]),
      },
    ],
  };
}
