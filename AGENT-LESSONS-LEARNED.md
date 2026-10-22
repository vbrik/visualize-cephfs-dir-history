# Agent Lessons Learned

- Before choosing a selection rule over CephFS own sizes (rbytes minus
  children's rbytes), run it against the real database. Parents' rbytes lag
  their children's within a sample, which makes false one-sample dips and
  ghost units. Only directories with changed children can dip that way, so
  smooth only those, or real one-point changes disappear too.
- `URLSearchParams` turns the lone surrogates of `os.fsdecode` into U+FFFD,
  so non-UTF-8 paths cannot round-trip through it. Carry paths in URLs as
  their raw bytes, percent-encoded (see `model.encodeValue`).
- ECharts fires no click event on a line series' filled area. Hit-test with
  `chart.getZr().on("click")` and `convertFromPixel` instead. Dispatch
  `hideTip` before replacing series, or an open tooltip throws.
- When the Claude in Chrome extension is not connected, Playwright can drive
  the installed Chrome (`chromium.launch(executable_path=...)`) with no
  browser download.
- A CSS `display` rule (e.g. `display: flex`) overrides the `hidden`
  attribute. Add `[hidden] { display: none !important; }`.
