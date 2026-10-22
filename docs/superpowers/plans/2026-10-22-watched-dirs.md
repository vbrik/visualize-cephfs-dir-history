# Watched Directories View Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `dirhistory-generate --watch FILE` writes the rbytes series of directories matched by watch patterns, and the page gets a "Watched" view: one trend card per watched dir, in config order, with a time-range slider shared with the Units view.

**Architecture:** A new pure module `src/dirhistory/watch.py` parses the TOML config, matches paths per component and orders/caps the matches. `history.summarize` feeds it every path during its existing single pass and adds the watched dirs and their ancestors to the fetched and kept rbytes. On the page, `model.trendCards` shapes cards, `charts/trendCard.js` builds one ECharts option per card, and `main.js` switches views via `view=` in the URL hash.

**Tech Stack:** Python 3.12 (`tomllib`, `fnmatch`), pytest, ruff; plain ES modules, vendored ECharts, Playwright smoke test (`scripts/check_web.py`).

**Spec:** `docs/superpowers/specs/2026-10-22-watched-dirs-design.md`

## Global Constraints

- Never create git commits or branches (AGENTS.md.d/GIT-v1.md): each task ends by *proposing* a commit subject and body that records the design choices and intentions behind the change.
- No new dependencies: `tomllib`, `fnmatch` from the standard library; ECharts stays vendored.
- Paths are `bytes` relative to ROOT (`b"."`) in Python; `str` only in JSON via `os.fsdecode`, `ensure_ascii=True` (docs/conventions.md). Terminal messages use `generate.display` for paths.
- `history.py` and `watch.py` are pure (no DB, no I/O except `watch.load` reading its file); `db.py` only queries; `generate.py` wires.
- Directory names are untrusted on the page: `textContent`, or `escapeHtml` in ECharts tooltip HTML.
- Colors come from CSS custom properties in `web/style.css`, light and dark.
- JS: 2-space indent, double quotes. Python: `ruff format && ruff check` clean.
- `venv/bin/pytest` and `venv/bin/python scripts/check_web.py` must pass at the end of every task that touches their area.
- Real database for manual verification: pass it as `TRACKDIR_DB` (the user gave the URL in the session; never write the password to a file). Real directory paths must not enter git: fixtures, docs, examples and commit messages use invented paths. `web/data/` is gitignored.
- Copy fixed by the spec:
  - Watched header: "Each card is a watched directory's whole size (rbytes, including its subdirectories) over the shown range. Each card has its own scale: compare shapes, not heights."
  - No-watched-dirs note: "No watched directories in this tree. See `dirhistory-generate --watch`."
- Defaults: `--max-watched` 100 per tree; view `units`.

## Review Focus

1. A watched dir that never changed after the first sample (most watched dirs at a coarse level are busy, but some are static): it must still get a full series, not be missing from `rbytes`. Test in Task 2.
2. A pattern targeting a tree not selected by `--tree`: no "matches nothing" warning. Test in Task 3.
3. A card whose dir is absent over the whole shown range (deleted before it, or created after it): card renders with "absent" instead of a size, no exception, no NaN in the scale. Check in Task 4.
4. Zooming the Watched slider and switching to Units (and back): both charts show the same range, and no datazoom feedback loop (a render storm or a stuck slider). Check in Task 5.
5. 100 cards (the cap) on a slider drag: the page stays responsive. Check in Task 6 against the real tree with the most matches.

---

### Task 1: Watch config parsing and matching (`watch.py`)

**Files:**
- Create: `src/dirhistory/watch.py`
- Test: `tests/test_watch.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `type Parts = tuple[bytes, ...]`: path components.
  - `MAX_WATCHED = 100`: default cap of watched dirs per tree (named constant with a one-line meaning).
  - `@dataclass(frozen=True, slots=True) class WatchPattern: text: str; parts: Parts` with
    `def for_tree(self, fs_path: bytes) -> Parts | None`: the pattern's components below FS_PATH when its first components match all of FS_PATH's components, else None. `fs_path` `b"/"` has no components.
  - `def parse_pattern(text: str) -> WatchPattern`: raises `ValueError` naming the pattern for: not starting with `/`; an empty component (`//`, trailing `/`; `"/"` alone is valid, parts `()`); a `.`, `..` or `**` component.
  - `def load(path: Path) -> list[WatchPattern]`: reads TOML; raises `ValueError` (TOMLDecodeError is one) for syntax, a top-level key other than `watch`, a missing `watch`, a `watch` that is not a list of strings, or any bad pattern; `OSError` for an unreadable file. Messages name the file.
  - `class Matcher`: `__init__(self, patterns: Sequence[Parts | None])` (None: pattern does not apply to this tree); `add(self, path: bytes) -> None` (path relative to ROOT; ROOT is `b"."` with 0 components); `matches(self) -> list[list[bytes]]`: per pattern, matching paths sorted, no duplicates within a pattern.
  - `@dataclass(frozen=True, slots=True) class Watched: paths: list[bytes]; found: list[int]; dropped: int` — `paths` in display order after the cap, `found` per pattern before dedup and cap, `dropped` how many unique paths the cap cut.
  - `def select(matches: list[list[bytes]], limit: int) -> Watched`: config order, first pattern wins on duplicates, then the first LIMIT.

- [ ] **Step 1: Write the failing tests** in `tests/test_watch.py`:

```python
def test_parse_pattern_splits_components():
    assert parse_pattern("/npx/ana/*/filtered").parts == (b"npx", b"ana", b"*", b"filtered")
    assert parse_pattern("/").parts == ()

@pytest.mark.parametrize("text", ["npx/ana", "", "/a//b", "/a/", "/a/./b", "/a/../b", "/a/**/b"])
def test_parse_pattern_rejects(text):
    with pytest.raises(ValueError, match=re.escape(repr(text))):
        parse_pattern(text)

def test_for_tree_strips_the_tree_part():
    p = parse_pattern("/npx/*/IceCube/*")
    assert p.for_tree(b"/npx/ana") == (b"IceCube", b"*")
    assert p.for_tree(b"/npx") == (b"*", b"IceCube", b"*")
    assert p.for_tree(b"/sim") is None
    assert parse_pattern("/npx/ana").for_tree(b"/npx/ana") == ()
    assert parse_pattern("/npx").for_tree(b"/npx/ana") is None  # shorter than the tree
    assert parse_pattern("/a").for_tree(b"/") == (b"a",)

def test_matcher_matches_per_component():
    m = Matcher([(b"*",), (b"a", b"*"), None, ()])
    for path in [b".", b"a", b"a/b", b"a/b/c", b"z"]:
        m.add(path)
    assert m.matches() == [[b"a", b"z"], [b"a/b"], [], [b"."]]  # "*" never crosses "/"

def test_matcher_matches_names_that_are_not_utf8():
    m = Matcher([(b"caf*",)])
    m.add(b"caf\xe9")
    assert m.matches() == [[b"caf\xe9"]]

def test_matcher_ignores_repeated_adds():
    m = Matcher([(b"*",)])
    m.add(b"a")
    m.add(b"a")
    assert m.matches() == [[b"a"]]

def test_select_keeps_config_order_and_first_occurrence():
    w = select([[b"b", b"c"], [b"a", b"b"]], limit=10)
    assert w == Watched([b"b", b"c", b"a"], [2, 2], 0)

def test_select_caps_and_counts_dropped():
    assert select([[b"a", b"b", b"c"]], limit=2) == Watched([b"a", b"b"], [3], 1)

def test_load_reads_patterns(tmp_path):
    f = tmp_path / "watch.toml"
    f.write_text('watch = ["/a/*", "/b"]\n')
    assert [p.text for p in load(f)] == ["/a/*", "/b"]

def test_load_accepts_an_empty_list(tmp_path):
    f = tmp_path / "watch.toml"
    f.write_text("watch = []\n")
    assert load(f) == []

@pytest.mark.parametrize("content", ['watched = ["/a"]\n', 'watch = "/a"\n', "watch = [1]\n", "watch = [\n", "", 'watch = ["a"]\n'])
def test_load_rejects(tmp_path, content):
    f = tmp_path / "watch.toml"
    f.write_text(content)
    with pytest.raises(ValueError, match=re.escape(str(f))):
        load(f)

def test_load_missing_file_is_an_oserror(tmp_path):
    with pytest.raises(OSError):
        load(tmp_path / "nope.toml")
```

- [ ] **Step 2: Run** `venv/bin/pytest tests/test_watch.py -q`. Expected: collection error (no module `dirhistory.watch`).

- [ ] **Step 3: Implement `src/dirhistory/watch.py`** per the Interfaces block. Module docstring: what a watch pattern is and that matching is per component. `parse_pattern` uses `os.fsencode` per component. `Matcher.add` prefilters by component count (`0 if path == b"." else path.count(b"/") + 1`) against the set of pattern lengths before calling `fnmatch.fnmatchcase` per component; keep matches in per-pattern sets and sort in `matches()`.

- [ ] **Step 4: Run** `venv/bin/pytest tests/test_watch.py -q && venv/bin/ruff format && venv/bin/ruff check`. Expected: all pass, ruff clean.

- [ ] **Step 5: Propose a commit** (do not run git commit): subject `Add watch patterns: parse a TOML watch list and match it per component`; body states why per-component matching (`*` never crosses `/`, no `**`, bounded by max_depth), why config errors fail instead of warn, why first pattern wins on duplicates.

---

### Task 2: Watched dirs in `summarize`

**Files:**
- Modify: `src/dirhistory/history.py` (`TreeHistory`, `summarize`, `_children`)
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: `watch.Matcher`, `watch.select`, `watch.Watched`, `watch.MAX_WATCHED`, `watch.Parts` (Task 1).
- Produces:
  - `TreeHistory` gains a last field `watched: Watched = Watched([], [], 0)` (default keeps existing constructors in tests working; use `field(default_factory=...)` since the dataclass is frozen with a mutable default).
  - `summarize(samples, changed, first, fetch, threshold, limit, resolution=DEFAULT_RESOLUTION, watch: Sequence[Parts | None] = (), watch_limit: int = MAX_WATCHED) -> TreeHistory`.
  - `TreeHistory.rbytes` keys: ROOT, units, watched paths, and all ancestors of units and watched paths.

- [ ] **Step 1: Write the failing tests** in `tests/test_history.py`. Extend the local `run` helper with `watch=()` and `watch_limit=100` passed through.

```python
def test_never_changed_watched_dir_gets_a_constant_series():
    rows = [(1, ROOT, 100, False), (1, b"p", 40, False), (1, b"p/q", 30, False),
            (1, b"z", 10, False), (2, ROOT, 110, False), (2, b"z", 20, False)]
    h = run(rows, watch=[(b"p", b"*")])
    assert h.watched.paths == [b"p/q"]
    assert h.rbytes[b"p/q"] == [30, 30]
    assert h.rbytes[b"p"] == [40, 40]  # ancestors kept, so Units crumbs work

def test_deleted_watched_dir_turns_absent():
    rows = [(1, ROOT, 100, False), (1, b"d", 50, False),
            (2, ROOT, 50, False), (2, b"d", None, True)]
    h = run(rows, watch=[(b"d",)])
    assert h.watched.paths == [b"d"]
    assert h.rbytes[b"d"] == [50, None]

def test_dir_created_later_can_be_watched():
    rows = [(1, ROOT, 100, False), (2, ROOT, 150, False), (2, b"new", 50, False)]
    h = run(rows, watch=[(b"*",)])
    assert h.watched.paths == [b"new"]
    assert h.rbytes[b"new"] == [None, 50]

def test_watched_order_found_and_cap():
    rows = [(1, ROOT, 9, False)] + [(1, p, 1, False) for p in (b"a", b"b", b"c")]
    h = run(rows, n=1, watch=[(b"c",), (b"*",), None], watch_limit=2)
    assert h.watched.paths == [b"c", b"a"]
    assert h.watched.found == [1, 3, 0]
    assert h.watched.dropped == 1

def test_watching_root():
    h = run([(1, ROOT, 100, False)], watch=[()])
    assert h.watched.paths == [ROOT]

def test_no_watch_keeps_only_units_and_their_ancestors():
    rows = [(1, ROOT, 100, False), (1, b"a", 50, False), (1, b"a/b", 20, False),
            (1, b"z", 10, False), (2, ROOT, 110, False), (2, b"a", 60, False),
            (2, b"a/b", 30, False)]
    h = run(rows, threshold=5)
    assert set(h.rbytes) == {ROOT, b"a", b"a/b"}
    assert h.watched == Watched([], [], 0)
```

- [ ] **Step 2: Run** `venv/bin/pytest tests/test_history.py -q`. Expected: the new tests fail (`unexpected keyword argument 'watch'`).

- [ ] **Step 3: Implement.** In `summarize`: build `Matcher(watch)`; pass its `add` into `_children` so every first-sample path (including ROOT) is offered in the same pass; offer every changed path after that; `watched = select(matcher.matches(), watch_limit)`. Add `watched.paths` and their ancestors to `wanted` before calling `fetch`, and to `kept`. Update the `summarize` and `TreeHistory` docstrings (watched dirs, why ancestors are kept).

- [ ] **Step 4: Run** `venv/bin/pytest -q && venv/bin/ruff format && venv/bin/ruff check`. Expected: all pass.

- [ ] **Step 5: Propose a commit**: subject `Track watched dirs in summarize`; body: matching rides the single first-sample pass (memory and time unchanged); watched dirs are added to the fetch set because unchanged paths are otherwise never fetched; ancestors kept so the page's crumbs are valid `in` values.

---

### Task 3: `--watch`, `--max-watched`, data files, warnings, deploy and generator docs

**Files:**
- Modify: `src/dirhistory/generate.py`, `deploy/dirhistory.service`, `README.md` (options paragraph, Deploy), `docs/design.md` (new decision "Watched directories", data-file tables), `docs/terms.md`
- Create: `deploy/watch.toml.example`
- Test: `tests/test_generate.py`

**Interfaces:**
- Consumes: `watch.load`, `watch.WatchPattern`, `watch.MAX_WATCHED` (Task 1); `summarize(..., watch=, watch_limit=)` and `TreeHistory.watched` (Task 2).
- Produces:
  - Tree file key `watched`: list of `os.fsdecode`'d paths in `TreeHistory.watched.paths` order (`[]` when none).
  - `trees.json` entry key `watched`: `len(history.watched.paths)`.
  - `def watch_warnings(patterns: Sequence[WatchPattern], applied: Sequence[bool], found: Sequence[int], all_trees: bool) -> list[str]`: per pattern at most one message: `WARNING: watch pattern '<text>' matches no directory` when applied and found is 0; `WARNING: watch pattern '<text>' applies to no tree` when not applied and ALL_TREES. Pure.
  - `read_tree(conn, tree, threshold, limit, res, watch: Sequence[Parts | None] = (), watch_limit: int = MAX_WATCHED)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_generate.py`:

```python
def test_tree_document_and_index_entry_carry_watched():
    h = TreeHistory(HISTORY.times, HISTORY.rbytes, HISTORY.units, Watched([b"caf\xe9", ROOT], [2], 0))
    assert tree_document(TREE, h, 10, T2)["watched"] == ["caf\udce9", "."]
    assert index_entry(TREE, h)["watched"] == 2

def test_tree_document_without_watch_has_empty_list():
    assert tree_document(TREE, HISTORY, 10, T2)["watched"] == []

def test_watch_warnings():
    ps = [parse_pattern(t) for t in ("/a/x", "/b/*", "/c")]
    assert watch_warnings(ps, [True, True, False], [0, 3, 0], all_trees=True) == [
        "WARNING: watch pattern '/a/x' matches no directory",
        "WARNING: watch pattern '/c' applies to no tree",
    ]
    assert watch_warnings(ps, [True, True, False], [0, 3, 0], all_trees=False) == [
        "WARNING: watch pattern '/a/x' matches no directory",
    ]

def test_bad_watch_file_is_an_error_before_the_database(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: pytest.fail("connected"))
    f = tmp_path / "w.toml"
    f.write_text('watched = ["/a"]\n')
    assert generate.main(["--db", "x", "--watch", str(f)]) == 1
    assert capsys.readouterr().err.startswith("ERROR: ")

def test_missing_watch_file_is_an_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: pytest.fail("connected"))
    assert generate.main(["--db", "x", "--watch", str(tmp_path / "nope.toml")]) == 1
    assert "nope.toml" in capsys.readouterr().err

@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_max_watched_must_be_positive(value, capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["--max-watched", value])
    assert "--max-watched" in capsys.readouterr().err
```

- [ ] **Step 2: Run** `venv/bin/pytest tests/test_generate.py -q`. Expected: new tests fail.

- [ ] **Step 3: Implement in `generate.py`:**
  - Options: `--watch FILE` (help: "TOML file listing directories to chart in the Watched view: `watch = [\"/fs/path/*/dir\", ...]`, absolute paths with per-component wildcards (default: none)") and `--max-watched N` (`_positive_int`, default `MAX_WATCHED`, help "keep at most this many watched dirs per tree, in config order").
  - In `main`, after the resolution check and before connecting: `patterns = watch.load(args.watch) if args.watch else []`, inside the existing `try` so `ValueError`/`OSError` become `ERROR:`.
  - In `run`: per tree, `parts = [p.for_tree(tree.fs_path) for p in patterns]`; pass to `read_tree`; accumulate `applied[i] |= parts[i] is not None` and `found[i] += history.watched.found[i]`; after a tree, if `history.watched.dropped`, note `WARNING: tree <id> (<display path>): --max-watched <n> dropped <k> watched directories`. After all trees, print each of `watch_warnings(patterns, applied, found, not args.tree)` to stderr. With `-v`, add `, <k> watched` to the per-tree stats line.
  - `tree_document` adds `"watched"`; `index_entry` adds `"watched"`. Update the DESCRIPTION paragraph with one sentence on `--watch`.

- [ ] **Step 4: Deploy and docs.**
  - `deploy/watch.toml.example`: commented example with invented paths (`/data/projects/*/raw`, `/data/projects/alpha`), one line each on: absolute, per-component `*`/`?`/`[...]`, no `**`, config order is display order.
  - `deploy/dirhistory.service`: `ExecStart=... --watch /etc/dirhistory/watch.toml`; comment line: the file must exist (an empty `watch = []` is fine).
  - `README.md`: options sentence mentions `--watch`/`--max-watched`; Deploy step 2 copies `watch.toml.example` to `/etc/dirhistory/watch.toml` *before* enabling the timer (a missing file fails the run).
  - `docs/design.md`: decision "Watched directories" (why a config file, why per-component patterns, why rbytes not own size, why ancestors are kept); data-file tables: `watched` in tree files and `trees.json`, and the `rbytes` row becomes "ROOT, every unit, every watched dir, and every ancestor of these". Replace "design 2c" mention only if wording now conflicts.
  - `docs/terms.md`: rows for watch pattern, watched dir, trend card, Units view / Watched view (spec "Terms" table).

- [ ] **Step 5: Run** `venv/bin/pytest -q && venv/bin/ruff format && venv/bin/ruff check && venv/bin/dirhistory-generate --help`. Expected: pass; help shows both options.

- [ ] **Step 6: Propose a commit**: subject `Add --watch: write watched dirs' rbytes to the data files`; body: config read before the DB so mistakes fail fast; a missing file is an error (explicit option), with the deploy note; warnings relative to `--tree` so partial runs don't cry wolf; trees.json carries the count so the page can hide the view without fetching tree files.

---

### Task 4: Page model, card chart module and colors

**Files:**
- Modify: `web/js/model.js`, `web/style.css`
- Create: `web/js/charts/trendCard.js`
- Test: `scripts/check_web.py` (pure-function checks through `page.evaluate`)

**Interfaces:**
- Consumes: data file `watched` and `rbytes` (Task 3).
- Produces:
  - `model.trendCards(data, range) -> [{path, values, start, end, change, lo, hi}]` in `data.watched` order (missing `watched` key → `[]`). `values` is `data.rbytes[path]` (full series, nulls kept). For `[a, b] = range`: `start = values[a] ?? 0`, `end = values[b] ?? 0`, `change = end - start`; `lo`/`hi` = min/max of the non-null values in `a..b`, both `null` if none.
  - `trendCardOption(data, card, { colors, range }) -> ECharts option` in `web/js/charts/trendCard.js`. `colors`: `{grow, growFill, shrink, shrinkFill, flat, flatFill, surface, ink, muted, grid}`.
  - CSS tokens in `:root` and the dark block: `--grow`, `--grow-fill`, `--shrink`, `--shrink-fill`, `--flat`, `--flat-fill`.

- [ ] **Step 1: Write the failing checks** in `scripts/check_web.py`, a new `check_model(page, base)` run once (light) before `check_fixture`:

```python
cards = page.evaluate("""async () => {
  const m = await import('./js/model.js');
  const data = {times: ['t0','t1','t2'], watched: ['a', 'gone', 'flat'],
    rbytes: {a: [10, 30, 20], gone: [5, null, null], flat: [7, 7, 7]}};
  return m.trendCards(data, [0, 2]);
}""")
assert [c["path"] for c in cards] == ["a", "gone", "flat"]
assert (cards[0]["change"], cards[0]["lo"], cards[0]["hi"]) == (10, 10, 30)
assert (cards[1]["end"], cards[1]["change"]) == (0, -5)
assert cards[2]["lo"] == cards[2]["hi"] == 7
absent = page.evaluate("""async () => (await import('./js/model.js'))
  .trendCards({times: ['t0','t1'], watched: ['x'], rbytes: {x: [null, null]}}, [0, 1])""")
assert absent[0]["lo"] is None and absent[0]["hi"] is None
```

- [ ] **Step 2: Run** `venv/bin/python scripts/check_web.py`. Expected: problems reported (`trendCards is not a function`).

- [ ] **Step 3: Implement `model.trendCards`** with a JSDoc block in the style of `stack`.

- [ ] **Step 4: Colors.** Invoke the `dataviz` skill, then add the tokens. Start from the mock (light: grow line `#8c491a`, fill `#ffe1d0`; shrink line `#56633f`, fill `#e1eecc`; flat line `#82796a`, fill `#eee7db`) and choose dark steps (e.g. lines `#f6a06b` / `#aebf92` / `#a19786`, fills `#402310` / `#272e1b` / `#2e2b25`); run the skill's validator for line-vs-surface contrast in both schemes and adjust until it passes. Update the comment at the top of `style.css`.

- [ ] **Step 5: Implement `trendCardOption`.** One `line` series over the points `a..b` only (`[Date.parse(t), v]`, nulls as `null` → gaps, `connectNulls: false`), area filled; stroke/fill picked by the sign of `card.change` (grow/shrink/flat). `animation: false`, no axes labels, `grid` with zero padding, `xAxis` `type: "time"`, `show: false`; `yAxis` `show: false`, `min: lo`, `max: hi`; when `lo === hi` use `lo - 1`/`hi + 1` so the line sits mid-height; when `lo === null` an empty series. Tooltip `trigger: "axis"`, `confine: true`, formatter: escaped local time and `formatBytes(value)` or "absent". Reuse `escapeHtml`: move it from `stackedArea.js` into `model.js` as an export and import it in both chart modules (one copy).

- [ ] **Step 6: Run** `venv/bin/python scripts/check_web.py`. Expected: `0 problems`.

- [ ] **Step 7: Propose a commit**: subject `Add trend-card model and chart option for the Watched view`; body: change and scale over the shown range so zooming reveals shape; absent counts as 0 like units; direction colors instead of the 8-slot palette, so the card count is not limited to 8.

---

### Task 5: Watched view in the page

**Files:**
- Modify: `web/index.html`, `web/js/main.js`, `web/style.css`, `tests/data/web/tree-1.json`, `tests/data/web/trees.json`, `scripts/check_web.py`, `README.md` (Using the page), `docs/design.md` (Page section)

**Interfaces:**
- Consumes: `trendCards`, `trendCardOption`, CSS tokens, `escapeHtml` (Task 4); `watched` in tree files and `trees.json` (Task 3).
- Produces: hash key `view` (`watched`, omitted for `units`); element ids `view` (segmented control: two `button`s with `data-view` and `aria-pressed`), `cards` (grid), `watch-slider` (ECharts slider container), `watch-note` (no-watched-dirs note); body attribute `data-view="units|watched"`; classes `units-only` / `watched-only`.

- [ ] **Step 1: Fixture.** In `tests/data/web/tree-1.json` add `"watched": ["Z/y", "A", "caf\udce9"]` and rbytes series for `Z` and `Z/y` (e.g. `Z`: `[100 GiB, 100 GiB, 50 GiB]`, `Z/y`: `[60 GiB, 80 GiB, 30 GiB]` in bytes); `tests/data/web/trees.json` gets `"watched": 3`. Keep `Z` free of units.

- [ ] **Step 2: Write the failing checks** in `check_fixture`:

```python
def cards(page):
    return page.eval_on_selector_all("#cards .trend-card .name", "els => els.map(e => e.textContent)")

page.goto(f"{base}#tree=1")
page.wait_for_timeout(SETTLE_MS)
assert page.is_visible("#view"), "view switch hidden although the tree has watched dirs"
page.click("#view button[data-view=watched]")
page.wait_for_timeout(SETTLE_MS)
assert "view=watched" in page.evaluate("location.hash")
names = cards(page)
assert names[:2] == ["Z/y", "A"] and len(names) == 3, "cards not in config order"
assert names[2].startswith("caf"), "non-UTF-8 watched dir not shown"
assert page.is_hidden("#group") and page.is_hidden("#reset"), "unit controls shown in Watched view"
page.reload()
page.wait_for_timeout(SETTLE_MS)
assert len(cards(page)) == 3, "view lost on reload"
page.locator("#cards .trend-card", has_text="Z/y").click()
page.wait_for_timeout(SETTLE_MS)
h = page.evaluate("location.hash")
assert "view=" not in h and "in=Z/y" in h, "click-through did not open Units in the dir"
page.locator("#crumbs button", has_text="Z").first.click()
page.wait_for_timeout(SETTLE_MS)
assert "Z" in page.inner_text("#crumbs"), "crumb above a watched dir fell back to ROOT"
assert "no units" in page.inner_text("#summary"), "empty dir looks broken"
```

  (Changing tree keeps the view: the fixture has one tree, so `check_tree` checks it on real data in Task 6.)

  A second check `check_no_watched(page, base)`: `page.route("**/data/trees.json", ...)` serving the fixture's `trees.json` with `"watched": 0`; `goto(f"{base}#tree=1&view=watched")`; assert `#view` hidden and `#legend li` present (Units rendered). The no-watched-dirs note is checked on real trees in Task 6.

- [ ] **Step 3: Run** `venv/bin/python scripts/check_web.py`. Expected: problems (no `#view`).

- [ ] **Step 4: Implement `index.html`:** the segmented control next to Tree (`<div id="view" class="seg" role="group" aria-label="View">` with buttons "Units" and "Watched"); the header `<p>` gets class `units-only`, plus a `watched-only` `<p>` with the spec's copy; Group by, Bands, Min peak, Rest of tree, Reset labels get `units-only`; inside `main.card`: wrap crumbs/summary/chart-row/hidden/details in a `units-only` container; add `watched-only` elements `#watch-note` (spec copy, `hidden` toggled), `#cards` (grid) and `#watch-slider`.

- [ ] **Step 5: Implement `style.css`:** `body[data-view="watched"] .units-only, body[data-view="units"] .watched-only { display: none !important; }`; `.seg` buttons with `aria-pressed="true"` styled as selected; `#cards { display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr)); gap: 10px; }`; `.trend-card` per the mock (page-colored background, 12px radius, 12px 14px padding, header row: bold ellipsized name, muted size, change tag pushed right as a pill colored `--grow`/`--shrink`/`--flat` on its fill), 56px sparkline box, a small muted line with the scale's bottom and top; `cursor: pointer`, visible `:focus-visible` outline; `#watch-slider { position: sticky; bottom: 0; height: 48px; background: var(--surface); }`.

- [ ] **Step 6: Implement `main.js`:**
  - `DEFAULTS.view = "units"`; `readState` reads `view` (`"watched"` or `"units"`); `writeState` writes it only when not default.
  - `const hasWatched = () => index.some((t) => t.watched > 0)`; effective view is `watched` only if requested and `hasWatched()`; set `document.body.dataset.view`; hide `#view` when `!hasWatched()`; set `aria-pressed`.
  - `render()` branches: `renderUnits(s)` (today's body) or `renderWatched(s)`. When switching to units, call `chart.resize()` after the container is shown (it was 0×0 while hidden); same for the watch slider.
  - `renderWatched`: `cards = trendCards(data, range)`; if empty, show `#watch-note` and clear the grid. Otherwise build each card (a `div.trend-card`, `tabindex="0"`, `role="link"`, `title` = absolute path `${fs_path}/${path}` or `fs_path` for `.`; name via `textContent` = path relative to tree root, "." shown as the tree's fs_path; size `formatBytes(end)` or "absent" when `values[b]` is null; tag `formatDelta(change)`; scale line `formatBytes(lo) – formatBytes(hi)` or empty). Keep one ECharts instance per path in a `Map` (renderer `"svg"`), `setOption(trendCardOption(...), { notMerge: true })`, dispose instances whose path is gone. Click or Enter → `writeState({ view: "units", in: path })`.
  - Watch slider: an ECharts instance on `#watch-slider` with a time `xAxis`, an invisible `line` series of `data.rbytes["."]` (gives the slider its data shadow) and one `dataZoom` slider. Its `datazoom` handler is the same as the Units chart's (compute `indexRange`, re-render only if `range` changed); after rendering, `dispatchAction` the current range to the visible view's chart only. Factor the shared handler into one function used by both charts.
  - Tree change keeps `view` (already, since `writeState` merges); Reset unchanged (hidden in Watched).
  - `renderSummary`: when no units are under `inPath`, say `…: <total> in total, no units here (own size changed by ≥ …)`.
  - `resize` listener resizes the watch slider and every card instance too.

- [ ] **Step 7: Run** `venv/bin/python scripts/check_web.py`. Expected: `0 problems`.

- [ ] **Step 8: Docs.** README "Using the page" table: rows for View (Units / Watched) and Watched cards (click opens Units in that dir; own scale per card). `docs/design.md` Page section: view switch, why a separate view (unit controls do not apply), shared range, cards plot rbytes.

- [ ] **Step 9: Propose a commit**: subject `Add the Watched view: trend cards for watched dirs`; body: separate view so unit-only controls are hidden; range shared across views; sticky slider; config order; click-through to Units; Units summary says "no units here" for a dir without units.

---

### Task 6: Real-data verification

**Files:**
- Modify: `scripts/check_web.py` (`check_tree` covers the Watched view)
- No real paths committed anywhere.

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Extend `check_tree`:** after the existing checks, if `#view` is visible, open `#tree=<id>&view=watched`; read the tree's `watched` count from `data/trees.json` via `page.evaluate(fetch…)`; assert the card count equals it, or `#watch-note` visible when 0; if any card, click the first and assert `view=` gone and `in=` present (or ROOT for "."); go back and drag the watch slider's left handle (`page.mouse`) and assert cards re-render without console errors; then, still in the Watched view, select another tree in `#tree` (when there is more than one) and assert `view=watched` is still in the hash.

- [ ] **Step 2: Write a watch file outside the repo** at `$SCRATCH/watch.toml` (the session scratchpad), with patterns chosen after listing real directories per tree (query `trackdir.dir_state` of each tree's first sample at depth 1–2): one literal dir per tree, one `*` pattern per tree, one pattern that matches nothing, and one broad pattern on the tree with the most directories to hit the 100 cap.

- [ ] **Step 3: Generate real data:** `TRACKDIR_DB=<session URL> venv/bin/dirhistory-generate -v --watch $SCRATCH/watch.toml`. Expected: files written to `web/data/`; stderr shows the no-match warning, the cap warning on the broad tree, and `<k> watched` per tree.

- [ ] **Step 4: Run** `venv/bin/python scripts/check_web.py --shots $SCRATCH/shots`. Expected: `fixture and 4 real trees checked, 0 problems`. Look at the screenshots (light and a dark one for the Watched view) and at the 100-card tree while dragging the slider; record responsiveness (Review Focus 5). Shots stay in the scratchpad.

- [ ] **Step 5: Final gate:** `venv/bin/pytest -q && venv/bin/ruff format --check && venv/bin/ruff check && venv/bin/python scripts/check_web.py`. Then `git status` and `git diff` to confirm nothing under `web/data/` and no real path is staged or in docs.

- [ ] **Step 6: Propose a commit** for the `check_web.py` extension, and report to the user: warnings seen, card counts per tree, responsiveness at 100 cards, and any lessons for `AGENT-LESSONS-LEARNED.md`.
