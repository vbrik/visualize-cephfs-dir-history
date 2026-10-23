# Sets Drive the Stack and Trends Views Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace units with sets: `dirhistory-generate --sets FILE` stores the rbytes of each set's dirs and their subdirs down to a per-group `depth`, and the page's Stack view stacks those rbytes (split Depth levels down, Top bands by span) while the Trends view (was Watched) shows one card per set dir.

**Architecture:** `watch.py` becomes `sets.py` (new TOML format, implicit "Top level" set, a matcher that also collects descendants within depth). `history.summarize` drops unit selection and returns stored dirs' series, the sets' dirs and the `split` list. `generate.py` writes unchanged series as one number. On the page, `model.js` gets `normalize` and `stackBands`; `main.js` swaps Group by/Bands/Min peak/Rest for Set/Depth/Top; a 64-slot palette replaces the 8-slot one.

**Tech Stack:** Python 3.12 (`tomllib`, `fnmatch`), pytest, ruff; plain ES modules, vendored ECharts, Playwright smoke test (`scripts/check_web.py`).

**Spec:** `docs/superpowers/specs/2026-10-23-sets-stack-trends-design.md`

## Global Constraints

- No git branches (AGENTS.md.d/GIT-v1.md); never push. Commit messages record the design choices and intentions behind the change, and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- No new dependencies. ECharts stays vendored.
- Paths are `bytes` relative to ROOT (`b"."`) in Python; `str` only in JSON via `os.fsdecode`, `ensure_ascii=True`. Terminal messages use `generate.display`.
- `history.py` and `sets.py` are pure (only `sets.load` reads its file); `db.py` only queries; `generate.py` wires.
- Directory names are untrusted on the page: `textContent`, or `escapeHtml` in tooltip HTML.
- Colors come from CSS custom properties in `web/style.css`, light and dark.
- JS: 2-space indent, double quotes. Python: `venv/bin/ruff format && venv/bin/ruff check` clean.
- Real database: `TRACKDIR_DB` env only (the user gave the URL in the session; never write the password to a file). Real paths never enter git: fixtures, docs, examples and commit messages use invented paths.
- Values fixed by the spec: implicit set name `Top level`, pattern `*`, depth 0, first; `--max-dirs` default 20 000 per tree; Top default and max 64; Depth default 0; palette 64 slots, the first 8 today's; own-files band label `X/ bytes`; views `stack` (default) and `trends`.
- Removed everywhere: units, own-size series, `--threshold`, `--max-units`, `--watch`, `--max-watched`, Group by, Bands (the 8-cap field), Min peak, Rest of tree (control, band, tooltip line), Smaller units, `--band-smaller`, `--band-rest`. No "Other" band.
- `scripts/check_web.py` checks `web/data` too: Tasks 2–4 must not regenerate `web/data`; Task 5 regenerates it (`venv/bin/dirhistory-generate --sets <file>`) before running the checks.

## Review Focus

1. **Absent dirs:** a set dir or subdir that appears later or is deleted has `null` points; bands count it as 0, the own-files band never goes negative, and Trends shows "absent". Tests: Task 3 `test_deleted_set_dir_turns_absent`; Task 5 fixture check of `Z/ bytes` with absent `Z/y` at the first point.
2. **Non-UTF-8 names in the new URL keys:** `in`, `hide` of a dir band and of an own-files band (`caf\udce9/.`) survive reload. Test: Task 5 `check_fixture`.
3. **A set that matches nothing, in the Stack view:** shows "No directories match this set's patterns." instead of an empty chart that looks broken. Test: Task 5 `check_sets` with `set=Empty`.
4. **Out-of-range state in a bookmarked URL:** `depth=9` clamps to the deepest split level; `in` outside the current set's dirs (or `constructor`) is ignored; an unknown `view` (old `watched`/`units`) falls back to `stack`. Test: Task 5 `check_fixture`.
5. **The tree root as a set dir** (`match = ["."]`, depth ≥ 1): it is stored, in `split`, and its own-files band is labeled `<fs_path>/ bytes`. Tests: Task 3 `test_root_set_dir_splits`; Task 5 `check_model`.

---

### Task 1: 64-slot palette

**Files:**
- Modify: `web/style.css` (`--series-1..8` light and dark)
- Modify: `docs/conventions.md` (the "8-slot categorical palette … never cycle it" line)

**Interfaces:**
- Produces: CSS custom properties `--series-1` … `--series-64` in `:root` and in the dark block; `--band-smaller` and `--band-rest` stay until Task 5 removes them.

- [ ] **Step 1: Load the `dataviz` skill** and read its color method, validator and `references/palette.md`.
- [ ] **Step 2: Generate slots 9–64** for light and dark: the same lightness/chroma band as the existing 8, the existing 8 unchanged as slots 1–8, ordered so that slots near each other differ clearly (adjacent bands of a stack usually hold nearby slots). Keep the generator script in the scratchpad, not the repo.
- [ ] **Step 3: Run the skill's validator** on both 64-color lists against `--surface` of each theme. Expected: passes its contrast and adjacent-distinctness checks; record any slot it flags and regenerate it.
- [ ] **Step 4: Write the 112 new properties** into `web/style.css` and change conventions.md to: "Series use the 64-slot categorical palette in fixed order; at most Top (≤ 64) bands are drawn, so it never cycles; keep a band's color while filters change."
- [ ] **Step 5: Run `venv/bin/python scripts/check_web.py`.** Expected: all checks pass (the page still reads slots 1–8 only).
- [ ] **Step 6: Commit** `style.css` and `conventions.md` (why: the Stack view draws up to 64 bands).

### Task 2: `sets.py` — new sets file and depth-aware matcher

**Files:**
- Create: `src/dirhistory/sets.py` (by `git mv src/dirhistory/watch.py`), `deploy/sets.toml.example` (by `git mv deploy/watch.toml.example`)
- Test: `tests/test_sets.py` (by `git mv tests/test_watch.py`)

**Interfaces:**
- Produces:
  - `IMPLICIT_NAME = "Top level"`, `MAX_DIRS = 20_000` (name the decision in a one-line comment: guards a per-tree file against a depth or pattern that stores far more than meant).
  - `Pattern(text: str, parts: Parts)` (was `WatchPattern`); `parse_pattern(text) -> Pattern` unchanged in behavior.
  - `DirGroup(patterns: list[Pattern], depth: int)`; `DirSet(name: str, groups: list[DirGroup])` with method `patterns() -> list[tuple[Pattern, int]]` (each pattern with its group's depth, file order).
  - `IMPLICIT: DirSet` = `DirSet("Top level", [DirGroup([parse_pattern("*")], 0)])`.
  - `with_implicit(sets: Sequence[DirSet]) -> list[DirSet]`: the file's set named `Top level` if any, else `IMPLICIT`, then the others in file order.
  - `load(path: Path) -> dict[bytes, list[DirSet]]` (no implicit set added; that is `with_implicit`'s job).
  - `Matcher(sets: Sequence[DirSet])` with `wants: bool`, `add(path: bytes) -> None` (idempotent), `matches() -> list[list[list[bytes]]]` (per set, per pattern: exact matches, sorted), `stored() -> dict[bytes, int]` (every path within depth of a match → the largest remaining depth below it, ≥ 0), `counts() -> list[int]` (per set: distinct stored paths its patterns cover).
  - `SetDirs(name: str, paths: list[bytes], found: list[int])` (was `Watched`); `select(name, matches: list[list[bytes]]) -> SetDirs` (no limit; first occurrence wins, as now).
  - `TooManyDirs(ValueError)` with `found: int`, `limit: int`, `counts: list[tuple[str, int]]`.
  - Removed: `MAX_WATCHED`, `TooManyWatched`, `WatchSet`, `Watched`.

- [ ] **Step 1: Write the failing tests** (rename the file, keep the pattern tests, replace the rest):

```python
def test_load_reads_groups_with_depths(tmp_path):
    f = _toml(tmp_path, '[tree."/exp".set."s"]\ndirs = [\n'
              '  { match = ["a", "b"], depth = 0 },\n  { match = ["x*"], depth = 3 },\n]\n')
    (s,) = load(f)[b"/exp"]
    assert s.name == "s"
    assert [(p.text, d) for p, d in s.patterns()] == [("a", 0), ("b", 0), ("x*", 3)]

@pytest.mark.parametrize("content", [
    'dirs = []',                                  # empty dirs
    'dirs = [{ match = ["a"] }]',                 # depth missing
    'dirs = [{ match = ["a"], depth = -1 }]',
    'dirs = [{ match = ["a"], depth = 1.5 }]',
    'dirs = [{ match = ["a"], depth = true }]',   # bool is not an int here
    'dirs = [{ match = [], depth = 0 }]',
    'dirs = [{ match = ["a"], depth = 0, x = 1 }]',
    'dirs = ["a"]',
    'dirs = [{ match = ["/abs"], depth = 0 }]',
])
def test_load_rejects_bad_groups(tmp_path, content): ...  # ValueError naming tree and set

def test_load_points_watch_to_dirs(tmp_path):
    # watch = [...] in a set: error message contains 'dirs = [{ match = [' 

def test_with_implicit_comes_first():
    s = DirSet("s", [])
    assert with_implicit([s]) == [IMPLICIT, s]

def test_with_implicit_is_replaced_by_a_set_of_its_name():
    mine, top = DirSet("s", []), DirSet("Top level", [DirGroup([parse_pattern("*")], 2)])
    assert with_implicit([mine, top]) == [top, mine]

def test_matcher_stores_descendants_within_depth():
    m = Matcher([_set("s", ("a", 2))])
    for p in [b"a", b"a/x", b"a/x/y", b"a/x/y/z", b"b", b"b/x"]: m.add(p)
    assert m.stored() == {b"a": 2, b"a/x": 1, b"a/x/y": 0}
    assert m.matches() == [[[b"a"]]]

def test_matcher_largest_depth_wins_across_groups_and_sets():
    m = Matcher([_set("s", ("a", 0)), _set("t", ("*", 1))])
    for p in [b"a", b"a/x"]: m.add(p)
    assert m.stored() == {b"a": 1, b"a/x": 0}
    assert m.counts() == [1, 2]

def test_matcher_root_pattern_covers_children():
    m = Matcher([_set("s", (".", 1))])
    for p in [b".", b"a", b"a/b"]: m.add(p)
    assert m.stored() == {b".": 1, b"a": 0}

def test_matcher_ignores_repeated_adds():  # counts and stored unchanged by a second add
```

`_set(name, *pairs)` builds a `DirSet` with one group per `(pattern text, depth)`; `_toml` writes the text to a file and returns its path. Keep the existing `parse_pattern`, per-component and non-UTF-8 matcher tests, adapted to the new constructor.

- [ ] **Step 2: Run** `venv/bin/pytest tests/test_sets.py -q`. Expected: FAIL (import errors: `DirSet`, `with_implicit`, …).
- [ ] **Step 3: Implement** in `sets.py`. `_set` reads `dirs`; a `watch` key gets the hint `'watch' is the old format; use dirs = [{ match = [...], depth = N }]`; `depth` must be an `int` and not a `bool`, ≥ 0. Matcher: for a path of length L, a pattern of length P with depth D covers it when P ≤ L ≤ P + D and the first P components match; remaining depth = D − (L − P). Prefilter on the set of coverable lengths, as `add` does now. Count a path toward a set only on its first `add`. Update the module docstring (sets feed both views; `depth` decides what is stored).
- [ ] **Step 4: Rewrite `deploy/sets.toml.example`** with the spec's example format: comments on `dirs`/`match`/`depth` (0 = the dir only), the implicit `Top level` set and replacing it, largest depth wins, `--max-dirs`; invented paths only.
- [ ] **Step 5: Run** `venv/bin/pytest tests/test_sets.py -q` and ruff. Expected: PASS, clean. (`test_history.py`/`test_generate.py` still import the old names; they are fixed in Tasks 3 and 4.)
- [ ] **Step 6: Commit** (why: one sets file feeds both views; `depth` bounds the data file; `Top level` always exists).

### Task 3: `history.summarize` — stored dirs instead of units

**Files:**
- Modify: `src/dirhistory/history.py`, `src/dirhistory/db.py` (`sample_rows` → `sample_paths`)
- Test: `tests/test_history.py`

**Interfaces:**
- Consumes: Task 2's `DirSet`, `Matcher`, `select`, `SetDirs`, `TooManyDirs`, `MAX_DIRS`.
- Produces:
  - `TreeHistory(times: list[datetime], rbytes: dict[bytes, Series], sets: list[SetDirs], split: list[bytes])` (`split` sorted).
  - `summarize(samples, changed: set[bytes], first: Iterable[bytes], fetch, resolution=DEFAULT_RESOLUTION, sets: Sequence[DirSet] = (), max_dirs: int = MAX_DIRS) -> TreeHistory`. `first` now yields paths only.
  - `db.sample_paths(conn, sample_id) -> Iterator[bytes]` (`SELECT path …`, same server-side cursor).
  - Removed: `Unit`, `_children`, `_own`, `_running_median`, `_range`, the `threshold`/`limit`/`watch`/`watch_limit` parameters.

- [ ] **Step 1: Write the failing tests.** Change the `run` helper to `run(rows, n=None, step=…, sets=(), cap=100)` and `first` to paths. Delete the unit tests (`test_deep_growth…` through `test_one_point_spike…`, `test_no_watch_keeps_only_units…`) and the watch tests; keep the `choose_points`, `_replay` ordering and NULL-carry tests, adapted. New tests:

```python
def test_stored_dirs_reach_depth_below_each_set_dir():
    rows = [(1, ROOT, 9, False), (1, b"a", 5, False), (1, b"a/x", 3, False),
            (1, b"a/x/y", 1, False), (1, b"b", 4, False)]
    h = run(rows, sets=[ds("s", ("a", 1))])
    assert set(h.rbytes) == {b"a", b"a/x"}
    assert h.split == [b"a"]

def test_split_needs_all_subdirs_stored():
    # set t stores a/x only; a is in set s at depth 0: a is not split
    rows = [(1, ROOT, 9, False), (1, b"a", 5, False), (1, b"a/x", 3, False), (1, b"a/y", 2, False)]
    h = run(rows, sets=[ds("s", ("a", 0)), ds("t", ("a/x", 0))])
    assert h.split == []
    assert set(h.rbytes) == {b"a", b"a/x"}

def test_unchanged_set_dir_gets_a_constant_series(): ...   # [30, 30]
def test_deleted_set_dir_turns_absent(): ...               # [50, None]
def test_dir_created_later_is_stored(): ...                # [None, 50], also its subdir within depth
def test_set_dirs_order_and_found(): ...                   # as test_watched_order_and_found
def test_sets_are_separate(): ...                          # as test_watch_sets_are_separate
def test_root_set_dir_splits():
    h = run([(1, ROOT, 9, False), (1, b"a", 5, False)], sets=[ds("r", (".", 1))])
    assert h.sets[0].paths == [ROOT] and h.split == [ROOT] and set(h.rbytes) == {ROOT, b"a"}

def test_too_many_stored_dirs_raises_before_fetching():
    first = [ROOT, b"a", b"b", b"c"]
    with pytest.raises(TooManyDirs) as e:
        summarize(samples(1), set(), first, lambda paths: pytest.fail("fetched"),
                  sets=[ds("ok", ("a", 0)), ds("Users", ("*", 0))], max_dirs=2)
    assert (e.value.found, e.value.counts) == (3, [("ok", 1), ("Users", 3)])

def test_no_sets_stores_nothing():
    h = run([(1, ROOT, 9, False), (2, ROOT, 10, False)])
    assert h.rbytes == {} and h.sets == [] and len(h.times) == 2
```

`ds(name, *pairs)` builds a `DirSet` with one group per `(pattern, depth)`.

- [ ] **Step 2: Run** `venv/bin/pytest tests/test_history.py -q`. Expected: FAIL.
- [ ] **Step 3: Implement.** One pass: `Matcher.add` every first-sample path, then every changed path. `stored = matcher.stored()`; over `max_dirs` → `TooManyDirs` before `fetch`. `split = sorted({parent(p) for p in stored if p != ROOT and stored.get(parent(p), -1) >= 1})`. Fetch and `_replay` exactly the stored paths. Rewrite the module docstring and the `MIN_BUCKET` comment: lag now shows as one-point dips in own-files bands (computed by the page), not ghost units.
- [ ] **Step 4: Run** `venv/bin/pytest tests/test_history.py -q` and ruff. Expected: PASS, clean.
- [ ] **Step 5: Commit** (why: bands are set dirs' rbytes now; `split` lists only dirs whose subdirs are all stored, so a partly stored dir is never split).

### Task 4: `dirhistory-generate` — `--sets`, `--max-dirs`, compact data files

**Files:**
- Modify: `src/dirhistory/generate.py`, `deploy/dirhistory.service`
- Test: `tests/test_generate.py`

**Interfaces:**
- Consumes: Task 2 (`load`, `with_implicit`, `IMPLICIT`, `MAX_DIRS`, `TooManyDirs`, `SetDirs`, `DirSet`), Task 3 (`summarize`, `TreeHistory`, `db.sample_paths`).
- Produces (the page relies on these in Task 5):
  - `compact(series: Series) -> Series | int | None`: one value if all points are equal, else the list.
  - `tree_document(tree, history, generated) -> dict` with keys `generated`, `tree`, `times`, `rbytes` (compacted), `sets: [{name, paths}]`, `split: [path]`. No `threshold`, `units`, `watched`.
  - `index_entry(tree, history, generated) -> dict` with `sets: [{name, dirs}]`; no `units`, `watched`.
  - `tree_sets(config, all_trees) -> dict[int, list[DirSet]]`: `with_implicit` applied for **every** tree, not only those in the file.
  - `set_warnings(tree, sets, found: Sequence[SetDirs]) -> list[str]` (was `watch_warnings`), skipping `IMPLICIT` itself (an unreplaced implicit set never warns).

- [ ] **Step 1: Write the failing tests** (replace the watch/threshold/max-units tests):

```python
def test_compact():
    assert compact([5, 5, 5]) == 5
    assert compact([None, None]) is None
    assert compact([5, 6]) == [5, 6]
    assert compact([None, 5]) == [None, 5]

def test_sets_is_required(capsys):  # parser().parse_args(["--db", "x"]) -> SystemExit; "--sets" in stderr
def test_threshold_and_max_units_are_gone(capsys):  # each -> SystemExit "unrecognized arguments"
def test_max_dirs_must_be_positive(value, capsys): ...  # "0", "-1", "x"

def test_run_writes_sets_split_and_compact_series(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    _fake_db(monkeypatch, {exp: [(ROOT, 9), (b"a", 5), (b"a/x", 3), (b"b", 4)]})
    sets = _write(tmp_path, '[tree."/exp".set.B]\ndirs = [{ match = ["a"], depth = 1 }]\n')
    assert generate.main(["--db", "x", "--out", str(tmp_path / "d"), "--sets", str(sets)]) == 0
    doc = json.loads((tmp_path / "d" / "tree-1.json").read_text())
    assert doc["sets"] == [{"name": "Top level", "paths": ["a", "b"]}, {"name": "B", "paths": ["a"]}]
    assert doc["split"] == ["a"]
    assert doc["rbytes"] == {"a": 5, "a/x": 3, "b": 4}
    assert "units" not in doc and "threshold" not in doc
    index = json.loads((tmp_path / "d" / "trees.json").read_text())
    assert index[0]["sets"] == [{"name": "Top level", "dirs": 2}, {"name": "B", "dirs": 1}]
    assert "WARNING" not in capsys.readouterr().err

def test_run_gives_trees_without_a_section_the_implicit_set(...)  # tree 2 absent from file -> sets == [Top level]
def test_empty_sets_file_is_valid(...)
def test_run_fails_when_stored_dirs_exceed_the_cap(monkeypatch, tmp_path, capsys):
    # --max-dirs 1 with Top level matching a, b:
    assert capsys.readouterr().err == (
        "ERROR: tree 1 (/exp): its sets store 2 directories, more than --max-dirs 1"
        " (Top level: 2). Lower their depths, narrow their patterns or raise --max-dirs.\n")
```

Adapt `test_run_keeps_patterns_to_their_own_tree`, `test_run_fails_for_a_section_without_a_tree`, `test_bad_…`/`test_missing_…file…` and the warning test (message: `WARNING: tree 2 (/sim): set 'Gone': pattern 'gone' matches no directory`) to `--sets`. `_fake_db` serves `sample_paths` instead of `sample_rows`.

- [ ] **Step 2: Run** `venv/bin/pytest -q`. Expected: FAIL in `test_generate.py` only.
- [ ] **Step 3: Implement.** `--sets FILE` (`required=True`; help: TOML of named sets of dirs per tree, `[tree."/tree/root".set."NAME"] dirs = [{ match = ["dir/*"], depth = N }]`, see `deploy/sets.toml.example`); `--max-dirs N` (default `MAX_DIRS`; help: fail if a tree's sets store more dirs than this, counting the dirs `depth` adds); drop `--threshold`, `--max-units`, `DEFAULT_THRESHOLD`, `DEFAULT_MAX_UNITS`, `parse_size` and `_size_arg` if nothing else uses them. `--min-bucket` help: "below 1h, rbytes propagation lag can make one-point dips in own-files bands". Rewrite `DESCRIPTION` (Stack view: rbytes of a set's dirs, split by depth; Trends view: one card per set dir; the sets file is required, an empty file gives each tree only the `Top level` set). Verbose line: `N points, M stored dirs in K sets, T s`. Error prefix `--sets:` where it said `--watch:`.
- [ ] **Step 4: Update `deploy/dirhistory.service`**: `--sets /etc/dirhistory/sets.toml`, and its comment (the file must exist, from `sets.toml.example`; an empty file gives only the `Top level` set).
- [ ] **Step 5: Run** `venv/bin/pytest -q` and ruff. Expected: all PASS, clean.
- [ ] **Step 6: Verify against the live DB** into the scratchpad (not `web/data`): `TRACKDIR_DB=… venv/bin/dirhistory-generate -v --sets <empty file> --out <scratchpad>/data`. Expected: each tree's file has `sets[0].name == "Top level"`, tree 4's has ~2 994 set dirs, and its size is far below 1 MB.
- [ ] **Step 7: Commit** (why: the sets file is now what decides the data; unchanged series as one number keep files small: 99.5 % of stored dirs never change).

### Task 5: Page — Stack and Trends views on sets

**Files:**
- Modify: `web/js/model.js`, `web/js/main.js`, `web/js/charts/stackedArea.js`, `web/index.html`, `web/style.css`
- Modify: `tests/data/web/trees.json`, `tests/data/web/tree-1.json`, `tests/data/web/tree-2.json`
- Modify: `scripts/check_web.py`

**Interfaces:**
- Consumes: Task 4's data format (`rbytes` values may be numbers or null; `sets`; `split`), Task 1's `--series-1..64`.
- Produces (`model.js`):
  - `MAX_BANDS = 64`.
  - `normalize(raw) -> data`: `rbytes` values expanded to arrays of `times.length`; `split: Set<string>`; `children: Map<string, string[]>` (sorted) for every dir in `split`.
  - `stackBands(data, {roots, base, depth, top, hidden, range}) -> {bands, cut}`: `roots` = the dirs to expand (the set's dirs, or `[in]`); `base` = `null` (labels relative to the tree root) or `in`; each band `{key, dir, own, label, values, drillable}`; `cut` = bands ranked past `top`.
  - Band rules: a dir less than `depth` below its root and in `split` becomes an own-files band (`key` `${dir}/.`, `own: true`, values `max(0, rbytes(dir) − Σ rbytes(children))`, nulls as 0) plus its children, recursively; any other dir is one band (`key` = `dir`, values = rbytes, nulls as 0). Rank by span over `range`, ties by key; keep the first `top`. `drillable` = `!own && split.has(dir)`.
  - Labels: `relative(dir, base ?? ROOT)`; ROOT, and `base` itself, label as its name (`data.tree.fs_path` for ROOT, else the last component); own-files bands add `/ bytes`.
  - Hidden: a band is hidden if `hidden` holds its key, or holds a dir key (not ending `/.`) that the band's `dir` is under (`isUnder`). Hidden bands are not ranked.
  - `maxDepth(data, roots) -> number`: levels of `split` below the deepest-splitting root.
  - `setDirOf(paths, path) -> string | null`: the longest of `paths` that `path` is under.
  - `reachable(data, paths, path) -> boolean`: `path` is one of `paths`, or its parent is in `split` and is itself reachable (so drilling can get there: `Q/r` is not reachable from Top level, since `Q` is not split).
  - Removed: `stack`, `hideKey` (a band's key is its hide key), `parseSize`, `ancestorAt` if unused.

- [ ] **Step 1: Regenerate the fixtures** (hand-edited JSON, invented paths). `tree-1.json` (`/fixture`, `max_depth` 3, 3 times; values in GiB × 2³⁰):

| path | rbytes |
|---|---|
| `A` | 500, 600, 700 |
| `A/b` | 250, 300, 350 |
| `A/c` | 100 (one number) |
| `caf\udce9` | 100, 150, 200 |
| `caf\udce9/x` | 100, 150, 200 |
| `Z` | 100, 100, 50 |
| `Z/y` | null, 80, 30 |
| `Q` | 40 (one number) |
| `Q/r` | 10 (one number) |

  `sets`: `Top level` [`A`, `Q`, `Z`, `caf\udce9`]; `Mixed` [`Z/y`, `A`, `caf\udce9`]; `Partial` [`Q/r`]; `Empty` []. `split`: [`A`, `Z`, `caf\udce9`] (not `Q`: only `Q/r` is stored). `tree-2.json`: one dir, `sets` = `Top level` only, empty `split`. `trees.json`: entries with `sets` counts, no `units`/`watched`.
- [ ] **Step 2: Rewrite the failing checks** in `scripts/check_web.py` (docstring included). `check_model` (in-page `import("./js/model.js")`, as now) asserts, on the fixture:
  - `stackBands` with Top level roots, depth 0: keys `A`, `Q`, `Z`, `caf\udce9`, `cut` 0.
  - Depth 1: keys include `A/.`, `A/b`, `A/c`, `Q` (unsplit), `Z/.`, `Z/y`, `caf\udce9/.`, `caf\udce9/x`; `A/.` at the last point is 250 GiB; `Z/.` at the first point is 100 GiB (absent `Z/y` counts 0); no value is negative.
  - `top: 2` → 2 bands and `cut` 2 at depth 0; hidden `["A"]` at depth 1 drops `A/.`, `A/b`, `A/c`; hidden `["A/."]` drops only `A/.`.
  - `maxDepth` = 1 for Top level, 0 for Partial; a ROOT root with ROOT in `split` labels its own band `/fixture/ bytes`.

  `check_fixture` (page): unknown tree shows the error; `in=constructor`, `in=Q/r` with `set` Top level, and `view=watched` are ignored (crumbs `Set Top level`, Stack view shown); `depth=9` shows Depth 1; Enter in Depth/Top does not reload; clicking legend `A` drills (`in=A`, Depth 1, crumbs `Set Top level / A`); hiding `caf…` dir band and `caf…/ bytes` survive reload, and chips bring them back; `top=2` shows "2 more not shown"; Reset restores Depth, Top, in, hide. `check_sets` (was `check_watch_sets`): Set menu lists `Top level — 4 dirs` first, changing set clears `in`; `set=Empty` shows "No directories match this set's patterns." in both views. Keep and rename the Trends checks (`check_trends`, sort/unchanged, resize, absent in range, slow fetches, refresh, refresh keeps zoom, click between points); a card click opens `view=stack&in=<dir>` with Depth 1, or Depth 0 for `Q`. `check_tree` (real data): per tree, light and dark: Depth 0, then Depth 1 if the max allows, drill into the first drillable band, hide a band, reload, unhide.
- [ ] **Step 3: Run** `venv/bin/python scripts/check_web.py`. Expected: FAIL (model functions missing, old controls).
- [ ] **Step 4: Implement `model.js`** per the Interfaces block; `trendCards` and `sortCards` unchanged (they read normalized arrays).
- [ ] **Step 5: Implement the page.**
  - `index.html`: `data-view="stack"`; classes `stack-only`/`trends-only` (and in `style.css`); controls: Tree, View (always shown: `Stack` | `Trends`), Set (both views), Depth (`<input id="depth" type="number" min="0" step="1">`), Top (`<input id="top" type="number" min="1" max="64">`), Reset (stack), Sort and Show unchanged (trends). Remove `#group`, `#min`, `#rest`, `#watch-note`; add `#stack-empty` and reuse one copy for both views: "No directories match this set's patterns." Header copy: Stack — "Which directories grow and shrink. Each band is a directory's whole size (rbytes), or, as `dir/ bytes`, the files directly in a directory that Depth splits. Bands add up unless the set holds a directory and its own subdirectory." Trends — "Each card is a set directory's whole size (rbytes, including its subdirectories) over the shown range. Each card has its own scale: compare shapes, not heights."
  - `main.js`: `DEFAULTS = {tree: null, view: "stack", set: null, depth: 0, top: MAX_BANDS, in: "", hide: [], sort: "span", flat: false}`; `STACK_KEYS = ["depth", "top", "in", "hide"]` (Reset). `view` is `trends` only when the hash says so. `normalize` the file in `load`. `in` is used only if `reachable(data, set.paths, in)`; Depth clamps to `maxDepth`. Tree change clears `in`, `hide`, `set`; set change clears `in`. Drilling (legend button or chart click on a drillable band) and crumbs to a dir set `in` and Depth 1; the `Set NAME` crumb clears `in` and sets Depth 0. Crumbs: `Set NAME` / the set dir (relative to the tree root, one crumb) / each component below it. Tree menu: `t.label` only. Summary: `<set name, or absolute(in)>: <total of drawn bands> in <n> bands[, <cut> more not shown (raise Top)]; set <name>: <k> dirs. Data generated <time>.` Legend and table: bands only. `colors()` reads 64 slots; drop `smaller`/`rest`.
  - `stackedArea.js`: layers are the bands only; tooltip rows per band plus "Total shown"; no Rest line; option signature `stackedAreaOption(data, model, {colors, range})`.
  - `style.css`: remove `--band-smaller`, `--band-rest`.
- [ ] **Step 6: Regenerate `web/data`** from the live DB with a sets file in the scratchpad that adds one depth-1 set, and run `venv/bin/python scripts/check_web.py`. Expected: every check passes, no console errors, for the fixture and every real tree.
- [ ] **Step 7: Commit** (why: bands are rbytes of set dirs split by Depth; Top replaces the 8-cap; no Other, Rest or Smaller units for now; old URL keys are ignored, not migrated).

### Task 6: Documentation

**Files:**
- Modify: `README.md`, `docs/design.md`, `docs/terms.md`, `docs/conventions.md`, `docs/superpowers/specs/2026-10-22-watched-dirs-design.md`, `AGENT-LESSONS-LEARNED.md` (only if a lesson came up)

- [ ] **Step 1: README:** intro (Stack bands are set dirs' rbytes, split by Depth; Trends cards), generate section (`--sets` required, `--max-dirs`, no threshold), deploy step 2 (`sets.toml` from `deploy/sets.toml.example`), the controls table (Tree, View, Set, Depth, Top, hide, Reset, Sort, Show unchanged, slider; no Group by/Bands/Min peak/Rest of tree), development note (`sets.py`).
- [ ] **Step 2: design.md:** replace "Stack own sizes", "Units", "Only changed paths are processed" (keep the streaming point, now for stored dirs), "Propagation lag" (own-files bands, clamped, measured 0.25 GiB over 2 days of hourly points, 8.6 GiB seen earlier at finer sampling), "Watched directories" → "Sets"; Page bullets (Top by span, no Other, drilling by `split`); Data files tables (as Task 4 produces); Risks (drop "Threshold is global" and "Magnitude hides change" if no longer true; add nested set dirs double-count, own-files lag dips, large depth makes large files).
- [ ] **Step 3: terms.md:** the spec's Terms table, removing the listed terms; conventions.md: model.js function names, `stack`/`trends` view names.
- [ ] **Step 4: Superseded note** at the top of the watched-dirs spec pointing to this spec.
- [ ] **Step 5: Check every claim against the code** (`grep -rn "unit\|watch\|threshold\|Rest of tree\|Smaller" README.md docs/*.md deploy/ web/ src/`); expected: only intended, current mentions remain.
- [ ] **Step 6: Run** `venv/bin/pytest -q`, ruff, `venv/bin/python scripts/check_web.py`. Expected: all pass.
- [ ] **Step 7: Commit** (why: docs describe sets, Stack and Trends; units are gone).
