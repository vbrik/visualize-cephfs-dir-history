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
- Playwright replaces lone surrogates (U+D800..U+DFFF) with U+FFFD when it
  passes strings between Python and the page, both ways. To check the URL
  codec with non-UTF-8 names, build the names inside `page.evaluate` and
  return only escaped text (e.g. `JSON.stringify`).
- `new TextDecoder("utf-8")` strips a leading U+FEFF by default, and when
  decoding one sequence at a time every sequence counts as leading. Pass
  `ignoreBOM: true` whenever the decoded text is data, such as file names.
- An ECharts instance per small multiple costs about 7 ms and 0.1 MB each:
  3000 sparklines took 20 s and 294 MB. Measure at the real count before
  choosing per-item charts; plain SVG paths in a fixed viewBox
  (`preserveAspectRatio="none"`, `vector-effect: non-scaling-stroke`) drew
  them in 0.6 s even at 400 points each.
- `page.wait_for_function` with a string predicate evals it, which the
  page's CSP (`default-src 'self'`, as check_web.py serves it) blocks; poll
  from Python instead.
- `scripts/check_web.py` also checks every tree in `web/data`. After changing
  a data file's format, regenerate `web/data` (`dirhistory-generate`, with
  `TRACKDIR_DB`) before running it, or its real-tree checks fail on the old
  files.
