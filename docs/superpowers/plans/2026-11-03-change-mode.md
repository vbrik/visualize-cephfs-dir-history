# Change Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A page-wide Size / Change toggle that charts each band's and card's rate of change (per day) instead of its value, in both views and both metrics, plus a Stack *Smooth* checkbox that removes one-point propagation-lag outliers from subtracted bands.

**Architecture:** All in the page; no generator or data-format change. `model.js` gains pure functions (`rates`, `smoothLag`, `formatRate`, `rateScale`) and new options on `stackBands` (`smooth`, `change`) and `trendCards` (rates and average rate per card). `stackedArea.js` draws signed step areas when given `change`; `layerAt` hit-tests a signed step stack; `trendCard.js` gains a step sparkline on a shared scale. `main.js` wires two new URL keys (`mode`, `smooth`) and the controls.

**Tech Stack:** Plain ES modules, vendored ECharts 5.6 (`stackStrategy: "samesign"` default, `step: "start"`), Playwright smoke test `scripts/check_web.py` (Python, drives installed Chrome).

**Spec:** `docs/superpowers/specs/2026-11-03-change-mode-design.md`

## Global Constraints

- No git branches (AGENTS.md.d/GIT-v1.md); never push. Commit messages record the design choices and intentions behind the change, and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Stage files by name (other sessions may share the tree); never `git commit -a`.
- No new dependencies; ECharts stays vendored at 5.6.
- Rate unit: per day. `DAY_MS = 86_400_000`. Rate at point i = (v[i] − v[i−1]) ÷ ((t[i] − t[i−1]) / DAY_MS), absent (`null`) counted as 0. Rate at point 0 is `null`.
- "Rates in range [a, b]" always means indexes a+1 … b (each point's rate covers its interval (t[i−1], t[i]]); a range of one point has none.
- Filters, *Show unchanged*, Top ranking, Trends sorts and grouping, legend sorts: on **values**, identical in both modes.
- URL keys: `mode=change` (absent: Size); `smooth=0` (absent: on). Any other value of either falls back to the default.
- Directory names are untrusted: `textContent`, or `escapeHtml` in tooltip HTML.
- JS: 2-space indent, double quotes. Python: `venv/bin/ruff format && venv/bin/ruff check` clean on `scripts/check_web.py`.
- Real paths never enter git. `web/data` holds real trees: measurements print to the terminal; only aggregate numbers go into docs.
- Check command for every task: `venv/bin/python scripts/check_web.py` → last line `fixture and N real trees checked, 0 problems`.

## Review Focus

1. **Dirs absent for part or all of the range:** creation and deletion are rate spikes, never `NaN`; a card absent throughout has all-zero rates and draws a flat line at zero. Tests: Task 1 `rates` absent case; Task 2 `never` card.
2. **A one-point range** (zoomed to a single point) in Change mode: no division by zero; average rate 0, scale [0, 0], no rate span. Test: Task 2 `onePoint`; Task 1 `stackBands` with `range: [2, 2]`.
3. **Everything shrinking:** the whole stack is below zero; the y axis goes negative and a click below zero drills into the band there. Test: Task 3 `layerAt` negative cases.
4. **Switching Size ↔ Change keeps the view:** tree, set, drill, Depth, hides, picks, range and the listed bands are unchanged; bogus `mode`/`smooth` values fall back. Test: Task 4 `check_change_mode`.
5. **Hover and click in a step block hit that block's point,** not the nearest point (which is half a bucket off). Test: Task 3 `layerAt` step cases; Task 4 tooltip hover check.

---

### Task 1: Rates, smoothing and Stack bands in the model

**Files:**
- Modify: `web/js/model.js` (new exports near `formatDelta`; `stackBands` options)
- Test: `scripts/check_web.py` (`check_model`)

**Interfaces:**
- Produces:
  - `DAY_MS` (number).
  - `rates(values, times) -> (number|null)[]`: per point, as in Global Constraints; `times` are ISO strings.
  - `formatRate(value, format = formatBytes) -> string`: `formatDelta(value, format) + "/d"`, e.g. `"+2.34 TiB/d"`, `"±0 B/d"`.
  - `smoothLag(diff, subtracted) -> number[]`: `diff` is a subtraction's unclamped values, `subtracted` the arrays it subtracted (absent as 0). For each i in 1 … n−2 where some `s` in `subtracted` has `s[i] !== s[i−1]`, the result's i is the median of the **input's** `diff[i−1], diff[i], diff[i+1]`; every other point is copied.
  - `stackBands(data, {..., smooth = false, change = false})`: with `smooth`, `ownFiles` and the hidden-bytes subtraction pass their unclamped result through `smoothLag` (with the kids' / removed arrays as `subtracted`) before the clamp at 0, over the whole history. With `change`, each returned band also has `rates` (`rates(band.values, data.times)`), and the returned bands (bottom first) are ordered by rate span over the range's rates (max − min of indexes a+1 … b, 0 if none), smallest at the bottom, ties by key. Which bands are returned is unchanged.

- [ ] **Step 1: Write the failing checks** in `check_model`, in a new `page.evaluate` block (build data with `m.normalize({...raw, times, series, sets, split})` as the `rooted` example does):

```python
# rates: per day, absent as 0, irregular spacing; point 0 has none.
assert got["rates"] == [None, 240, 120, 240]          # [0,10,20,30] at 0h,1h,3h,4h
assert got["ratesAbsent"] == [None, 120, -120]        # [null,5,null], hourly
assert got["formatRate"] == ["+2.34 TiB/d", "±0 B/d", "−1.00 k/d"]
#   formatRate(2400 * 2**30), formatRate(0), formatRate(-1000, m.formatCount)
# smoothLag: a lag dip and a lag bump go; real steps stay; ends untouched.
assert got["dip"] == [50, 50, 50, 50]       # parent [100,100,110,110], kid [50,60,60,60]
assert got["bump"] == [50, 50, 50, 50]      # parent [100,100,90,90],  kid [50,40,40,40]
assert got["ownStep"] == [50, 50, 60, 60]   # parent [100,100,110,110], kid constant 50
assert got["bothStep"] == [50, 60, 60, 60]  # parent [100,120,120,120], kid [50,60,60,60]
assert got["lastPoint"] == [50, 50, 40]     # parent [100,100,100], kid [50,50,60]
# stackBands smooth: own-files band of "." in a tree with series
#   ".": [100,100,200], "k": [90,190,190], split ["."]  -> values
assert got["ownRaw"] == [10, 0, 10]          # smooth off: clamped dip
assert got["ownSmooth"] == [10, 10, 10]      # smooth on: median before the clamp
# The same with "k" hidden under a set dir "." at depth 0 (hidden subtraction).
assert got["hiddenSmooth"] == [10, 10, 10]
# change: steady s [0,10,20,30,40], jumpy j [0,0,30,30,30], hourly, range [0,4].
assert got["sizeOrder"] == ["j", "s"]         # bottom first: value span 30 < 40
assert got["changeOrder"] == ["s", "j"]       # bottom first: rate span 0 < 720
assert got["changeTop1"] == ["s"]             # top: 1 keeps the larger value span
assert got["steadyKept"] == ["s", "j"]        # change: true, flat: false: the steady grower stays
assert got["sRates"] == [None, 240, 240, 240, 240]
assert got["onePointOrder"] == ["j", "s"]     # change: true, range [2, 2]: no rates, ties by key
```

- [ ] **Step 2: Run** `venv/bin/python scripts/check_web.py`. Expected: problems listing `m.rates is not a function` (or the first failing assertion).
- [ ] **Step 3: Implement** `DAY_MS`, `rates`, `formatRate`, `smoothLag` and the two `stackBands` options in `web/js/model.js`, per the Interfaces block. Extend `stackBands`'s doc comment with both options. `ownFiles` must keep subtracting `v ?? 0` and clamp after smoothing.
- [ ] **Step 4: Run** the check command. Expected: `0 problems`.
- [ ] **Step 5: Commit** `web/js/model.js scripts/check_web.py` — "Model: rates per day, lag smoothing, Change-mode stack order".

---

### Task 2: Trends cards and step sparklines in Change mode

**Files:**
- Modify: `web/js/model.js` (`trendCards`, new `rateScale`)
- Modify: `web/js/charts/trendCard.js` (new `sparkRatePaths`, `sparkStepAt`)
- Test: `scripts/check_web.py` (`check_model`, next to the `sparks` block)

**Interfaces:**
- Consumes: `rates`, `DAY_MS` (Task 1).
- Produces:
  - `trendCards(data, paths, range, metric = "rbytes")`: each card also has `rates` (`rates(values, data.times)`) and `rate`: `change` ÷ ((t[b] − t[a]) / DAY_MS), 0 when a = b.
  - `rateScale(cards, range) -> {lo, hi}`: lo = min(0, every card's rates a+1 … b), hi = max(0, same).
  - `sparkRatePaths(xs, card, range, scale) -> {line, fill, zero}`: steps; for each i in a+1 … b a flat segment from `xs[i−a−1]` to `xs[i−a]` at the rate's y, joined by vertical jumps (one path); `fill` closes the same outline to the zero line; `zero` is `"M 0 y0 L 1000 y0"`. y maps `scale` onto PAD … HEIGHT−PAD like `sparkPaths`; when lo = hi (= 0) everything sits at mid-height. Coordinates rounded to 0.1 as `sparkPaths` does. All three are `""` when a = b.
  - `sparkStepAt(xs, range, fraction) -> [index, x]`: the point whose interval holds the fraction (the first i in a+1 … b with `xs[i−a] ≥ fraction·WIDTH`; at fraction 0, a+1), and the middle x of that interval for the cursor. When a = b: `[a, xs[0]]`.

- [ ] **Step 1: Write the failing checks.** Reuse the `sparks` block's data (times 0h, 1h, 3h, 4h; `up` [0,10,20,30], `flat`, `gap` [1,2,null,3], `never`):

```python
assert c["upRate"] == 30 * 24 / 4                    # change 30 over 4 h, per day = 180
assert c["upRates"] == [None, 240, 120, 240]
assert c["scale"] == {"lo": -24, "hi": 240}          # gap: [1,2,0,3] -> 24, -24, 72
assert c["onePoint"] == {"rate": 0, "scale": {"lo": 0, "hi": 0},
                         "paths": {"line": "", "fill": "", "zero": ""}}
# up on scale [-24, 240]: y(v) = 53 - (v + 24) / 264 * 50; y(0) = 48.5, y(120) = 25.7
assert c["upPaths"]["line"] == "M 0 3 L 250 3 L 250 25.7 L 750 25.7 L 750 3 L 1000 3"
assert c["upPaths"]["zero"] == "M 0 48.5 L 1000 48.5"
assert c["upPaths"]["fill"].startswith("M 0 48.5 L 0 3") and c["upPaths"]["fill"].endswith("L 1000 48.5 Z")
# all-zero rates: every segment's start and end, none merged
assert c["neverPaths"]["line"] == "M 0 48.5 L 250 48.5 L 250 48.5 L 750 48.5 L 750 48.5 L 1000 48.5"
assert [i for i, _ in c["stepAt"]] == [1, 1, 2, 2, 3]   # fractions 0, 0.2, 0.3, 0.7, 0.9
assert c["stepAt"][2][1] == 500                          # middle of the 1h..3h interval
```

  Each segment contributes its start and end point, unmerged, so a run of equal rates still has its vertical (zero-height) joins.
- [ ] **Step 2: Run** the check command. Expected: failures naming `rateScale` / `sparkRatePaths`.
- [ ] **Step 3: Implement** per Interfaces.
- [ ] **Step 4: Run** the check command. Expected: `0 problems`.
- [ ] **Step 5: Commit** `web/js/model.js web/js/charts/trendCard.js scripts/check_web.py` — "Trends: card rates, shared rate scale, step sparklines".

---

### Task 3: Signed step chart and hit-testing

**Files:**
- Modify: `web/js/charts/stackedArea.js` (`stackedAreaOption`, `tooltip`, `row`)
- Modify: `web/js/model.js` (`layerAt`)
- Test: `scripts/check_web.py` (`check_model`)

**Interfaces:**
- Consumes: `band.rates` (Task 1), `formatRate` (Task 1).
- Produces:
  - `stackedAreaOption(data, model, {..., change = false})`: with `change`, each series plots `band.rates` (`null` as 0) with `step: "start"`, `lineStyle.width` 0 (no gaps; `gaps` is ignored), the y axis has no `min`, the axis labels use `formatRate`; and the tooltip, for the hovered point i, lists each band's rate at i (same 30-row limit, largest |rate| first), a "N smaller bands" row summing the rest, and a "Net" row (sum of all drawn bands' rates), with no "change since" note. Size mode is unchanged.
  - `layerAt(layers, times, ms, value, {step = false} = {})`: with `step`, i is the first index with t[i] ≥ ms (clamped to 1 … n−1; no interpolation); layers stack separately by sign (value ≥ 0 on the positive stack, from 0 up; < 0 on the negative stack, from 0 down, bottom-first order), and the layer whose span holds `value` is returned, else −1.
- The tooltip must show the block under the pointer (the point whose interval holds the pointer's time), not the nearest point: ECharts' axis trigger snaps to the nearest point, so find the right index yourself (e.g. `axisPointer.snap: false` and the pointer's axis value, or an index from `layerAt`'s rule). Verify which in the vendored build.
- Confirm in the vendored build that `step: "start"` draws point i's value over (t[i−1], t[i]] and that stacked step areas with mixed signs fill without diagonals; if `"start"` is the other way round, use `"end"` and say so in the commit message.

- [ ] **Step 1: Write the failing checks** for `layerAt` (times 0h, 1h, 2h):

```python
# layers bottom first: p [0, 10, 10], n [0, -5, 20], q [0, 4, -3]
# at 0.5 h (interval of point 1): positive stack p 0..10, q 10..14; negative n 0..-5
assert got["stepAt"] == [0, 0, 2, 2, 1, 1, -1, -1]
#   values 1, 9.9, 10.5, 14, -0.1, -5, 14.1, -5.1
# at 1.5 h (point 2): p 0..10, n 10..30; q 0..-3
assert got["stepAt2"] == [1, 2, -1]                  # values 25, -2, -4
# step omitted: today's interpolating behavior; layers [[1,1,1],[2,2,2]] at 0.5 h, value 2
assert got["linear"] == 1
```

- [ ] **Step 2: Run** the check command. Expected: the `stepAt` assertion fails.
- [ ] **Step 3: Implement** `layerAt`'s `step` option and `stackedAreaOption`'s `change` option per Interfaces.
- [ ] **Step 4: Run** the check command. Expected: `0 problems`.
- [ ] **Step 5: Commit** `web/js/model.js web/js/charts/stackedArea.js scripts/check_web.py` — "Charts: signed step areas and their hit-testing for Change mode".

---

### Task 4: Page wiring: toggle, Smooth, both views

**Files:**
- Modify: `web/index.html` (controls, header texts, table head)
- Modify: `web/style.css` (`body[data-mode]` visibility rules, zero line of sparklines)
- Modify: `web/js/main.js`
- Test: `scripts/check_web.py` (new `check_change_mode`, run in `main`'s list; `check_tree` gains a Change-mode pass)

**Interfaces:**
- Consumes: everything from Tasks 1–3.
- Decisions this task fixes:
  - State: `mode: "size" | "change"` (`DEFAULTS.mode = "size"`), `smooth: boolean` (`DEFAULTS.smooth = true`), read and written as in Global Constraints. `smooth` joins `STACK_KEYS` (Reset restores it); `mode` does not.
  - Controls: a `#mode` segmented group (`data-mode="size"` "Size", `data-mode="change"` "Change"), right after `#metric`, title "Chart sizes, or how fast they change (per day)"; a `#smooth` checkbox labeled "Smooth", `stack-only`, title "Remove one-point dips and bumps that CephFS's delayed size updates leave in bands made by subtraction". `document.body.dataset.mode` set in `render`; CSS hides `.size-only` / `.change-only` like `.rbytes-only`.
  - Stack, Change mode: `stackBands(..., smooth: s.smooth, change: true)`, `stackedAreaOption(..., change: true)`, `layerAt(..., {step: true})` on `b.rates` in the click handler, `fitGaps` does nothing (and leaves `gaps` as it is). The legend's value and delta columns stay sizes. Table head and cells in Change mode: Band, Avg rate (`formatRate((end − start) ÷ days)`), Peak rate (largest rate in the range, `formatRate`), Low rate (smallest), Change (size, as now). Summary: `${where}: net ${rate} at the end in N bands…` with the net rate at point b. Chart aria-label: "Stacked step area of directory {noun} rates in {where}…".
  - Stack, Size mode: `smooth: s.smooth` too; everything else as today.
  - Trends, Change mode: `.size` shows `formatRate(card.rate)`, `.tag` the size change as now; sparkline from `sparkRatePaths` on `rateScale(cards, range)` of the **shown** (filtered) cards, plus a `.zero` path; `.scale` text: "shared scale {lo} – {hi}" in `formatRate`; header text "All cards share one scale: compare heights." (`change-only`) beside the existing "Each card has its own scale…" (`size-only`); hover uses `sparkStepAt` and shows `formatRate(card.rates[i])`; the slider's shadow is `rates` of `setTotal(set)` (null as 0).

- [ ] **Step 1: Write `check_change_mode(page, base)`** against fixture tree 1 (hourly, 3 points; A 500→600→700 GiB, café 100→150→200 GiB, Z 100→100→50 GiB):

```python
go(page, f"{base}#tree=1")
act(page, lambda: page.click("#mode button[data-mode=change]"))
assert "mode=change" in state(page), state(page)
# Same bands as Size mode, stacked by rate span (A, café 0; Z 50 GiB/h): legend top first
assert bands(page) == ["Z", caf, "A"], bands(page)
assert "net +2.34 TiB/d" in page.inner_text("#summary")      # +100 +50 −50 GiB per h at b
assert page.inner_text("#table-body").count("/d") >= 6
# Switching keeps everything (anti-goal) and back again drops only mode.
kept = "tree=1&set=Mixed&in=A&depth=1&hide=A/c&pick=A/b&sflat=1&smin=0"
go(page, f"{base}#{kept}")
act(page, lambda: page.click("#mode button[data-mode=change]"))
assert set(state(page).lstrip("#").split("&")) == {*kept.split("&"), "mode=change"}
act(page, lambda: page.click("#mode button[data-mode=size]"))
assert set(state(page).lstrip("#").split("&")) == set(kept.split("&"))
# Bogus values fall back.
go(page, f"{base}#tree=1&mode=bogus&smooth=x")
assert page.get_attribute("#mode button[data-mode=size]", "aria-pressed") == "true"
assert page.is_checked("#smooth")
# Smooth: unticking writes smooth=0; Reset restores it, keeps mode.
go(page, f"{base}#tree=1&mode=change")
act(page, lambda: page.uncheck("#smooth"))
assert "smooth=0" in state(page)
act(page, lambda: page.click("#reset"))
assert "smooth" not in keys(page) and "mode=change" in state(page)
# Tooltip: hovering 1/4 of the way from point 0 to 1 shows point 1's block
# (A +100 GiB/h, café +50, Z 0; net +150 GiB/h), not the nearer point 0's.
# (Find the pixel with chart.convertToPixel as check_click_between_points does.)
tip = tooltip_text_at(page, 0.25)
assert "+2.34 TiB/d" in tip and "Net" in tip and "+3.52 TiB/d" in tip, tip
# Trends: rates on the cards, one shared scale.
go(page, f"{base}#tree=1&view=trends&mode=change")
assert "+2.34 TiB/d" in card(page, "A").inner_text()          # avg (700−500)/2 h
scales = page.eval_on_selector_all("#cards .scale", "els => els.map(e => e.textContent)")
assert len(set(scales)) == 1 and scales[0].startswith("shared scale"), scales
assert page.is_visible("text=All cards share one scale")
```

  Add a helper `tooltip_text_at(page, fraction)` next to `tooltip_names`. In `check_tree`, after the Entries pass: `go(page, f"{base}#tree={tree}&mode=change")` and `…&mode=change&view=trends`, asserting bands or a note as the Size pass does (and a screenshot if `shots`).
- [ ] **Step 2: Run** the check command. Expected: `check_change_mode` fails at the `#mode` click.
- [ ] **Step 3: Implement** the HTML, CSS and `main.js` changes per "Decisions this task fixes".
- [ ] **Step 4: Run** the check command. Expected: `0 problems`, including every real tree in Change mode.
- [ ] **Step 5: Look at it.** Open `python -m http.server -d web 8000` in Chrome (or `--shots` screenshots): a real tree at Depth 1 in Change mode, light and dark; the step bands must meet at bucket edges with no diagonal slivers across zero. Note anything odd for Task 5.
- [ ] **Step 6: Commit** `web/index.html web/style.css web/js/main.js scripts/check_web.py` — "Page: Size / Change toggle and Smooth checkbox in both views".

---

### Task 5: Measure smoothing on real data; docs

**Files:**
- Create (scratchpad, not the repo): `measure_smooth.py`, serving `web/` with `web/data` as `check_web.py`'s `serve` does and calling `model.js` in the page
- Modify: `docs/design.md` (Propagation lag; Page: Change mode, Smooth; URL keys), `docs/terms.md` (rate, Change mode, Smooth), `README.md` (the toggle)

**Interfaces:**
- Consumes: `stackBands` with `smooth` (Task 1).

- [ ] **Step 1: Write and run the measurement.** For every real tree in `web/data`, every split dir's own-files band at Depth ≥ 1, both metrics, over the whole history: points `smoothLag` changes, the largest change it made (GiB / entries), and, with Smooth on, the largest one-point dip left (a point below both neighbors) and how many remain. Print aggregates only, no paths.
- [ ] **Step 2: Judge it.** If Smooth leaves dips that look two points wide (dip at i and i+1, recovered at i+2) in more than a handful of places, stop and report the numbers to the user before changing the window (spec).
- [ ] **Step 3: Update the docs.** design.md: Propagation lag says Smooth (default on) replaces "No smoothing", why (long-term trends, hourly samples at most), what it touches, and the measured numbers with date; Page gains a Change-mode bullet (rates per day, step areas and why, Top by size span, stacking by rate span, shared Trends scale and its burst risk) and lists `mode`, `smooth` among the URL keys. terms.md: rows for "rate", "Change mode", "Smooth". README: one sentence on Size / Change after the Entries sentence.
- [ ] **Step 4: Run** the check command. Expected: `0 problems`.
- [ ] **Step 5: Commit** `docs/design.md docs/terms.md README.md` — "Docs: Change mode, Smooth, and measured lag smoothing".
- [ ] **Step 6: Set the spec's status** to "implemented (2026-11-03)" and commit it with the docs or on its own.
