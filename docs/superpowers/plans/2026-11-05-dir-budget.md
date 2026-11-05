# Directory Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace `--max-dirs`'s failure with a per-tree budget from the sets file: a tree over budget is stored breadth-first and saved partial, its pruned dirs listed in the data file and marked `*` in the page.

**Architecture:** `sets.load` resolves each configured tree's budget (`TreeConfig`). A pure `sets.cut(stored, budget)` picks the kept dirs level by level; `history.summarize` applies it where the `max_dirs` check was, before fetching series, and reports the cut on `TreeHistory`. `generate` runs only configured trees, writes `pruned`, and warns. The page reads `pruned` and appends `*` to those dirs' names wherever it shows them.

**Tech Stack:** Python 3.12+ (psycopg 3, tomllib, pytest, ruff), plain ES modules, Playwright smoke test `scripts/check_web.py`.

**Spec:** `docs/superpowers/specs/2026-11-05-dir-budget-design.md`

## Global Constraints

- No git branches; never push. Stage files by name, never `git commit -a` (other sessions may share the tree). Commit messages record design choices and intentions, and end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Python: `venv/bin/ruff format src tests scripts && venv/bin/ruff check src tests scripts` clean; `venv/bin/pytest -q` passes at the end of every task.
- Messages: errors `ERROR: ...` (via `generate._error`), warnings start `WARNING: tree ID (ROOT): `, paths via `generate.display`, numbers printed plain (no separators).
- Path order is byte order (`sorted()` on `bytes`).
- `budget`: an `int` ≥ 1, not a `bool`. No CLI option for it; no built-in default.
- Data file key `pruned`: always present, sorted, `os.fsdecode`d. Old files without it: nothing pruned.
- Page: directory names are untrusted (`textContent`; `escapeHtml` in tooltip HTML). `PRUNED_MARK = "*"`; `PRUNED_TITLE = "Subdirectories not stored: the tree's budget ran out"`.
- Real paths never enter git (tests use `/exp`, `/sim`, `/x`-style roots already in the tests; docs use aggregate numbers only).
- Page check command: `venv/bin/python scripts/check_web.py` → last line `fixture and N real trees checked, 0 problems` (real-tree problems from the large files are reported, not fixed: Task 6).

## Review Focus

1. **A budget smaller than the tree's top level** (e.g. 2 997 set dirs, budget 1 000): set dirs without a stored parent are units of one, so the first 1 000 by path are kept, not none. Test: Task 2 `test_cut_fills_unparented_set_dirs_one_by_one`.
2. **A set dir nested under another set's stored dir** (fixture's `Q` depth 0 with set dir `Q/r`): `Q/r` cut does not mark `Q` pruned. Test: Task 2 `test_cut_does_not_prune_a_dir_stored_at_depth_0`.
3. **Leaves at the tree's `max_depth`** (a `*` depth-5 set on a max_depth-5 tree): level-5 dirs are kept without being pruned when nothing below them exists. Test: Task 2 `test_cut_never_prunes_dirs_without_candidate_subdirs`.
4. **A budget exactly equal to the candidates, or to a level boundary**: no cut, no warning, `pruned == []`. Tests: Task 2 `test_cut_exact_budget_is_no_cut`, Task 3 `test_run_does_not_warn_at_exact_budget`.
5. **Operators with an old sets file or service**: an empty file, a file without `budget`, or `--max-dirs` fail with a message that says what to add. Tests: Task 1 `test_load_rejects_missing_budget_and_names_both_places`, Task 3 `test_removed_options_are_gone` (`--max-dirs`).

---

### Task 1: Budgets in the sets file; only configured trees run

**Files:**
- Modify: `src/dirhistory/sets.py` (module docstring, `load`, `_sets`, new `TreeConfig`, new `_budget`)
- Modify: `src/dirhistory/generate.py` (`tree_sets` → `configured`, `select_trees`, `merged_index`, `run`, `read_tree`, `DESCRIPTION`, `--sets` help)
- Test: `tests/test_sets.py`, `tests/test_generate.py`

**Interfaces:**
- Produces:
  - `sets.TreeConfig` — `@dataclass(frozen=True, slots=True) class TreeConfig: budget: int; sets: list[DirSet]`
  - `sets.load(path: Path) -> dict[bytes, TreeConfig]` (sets without the implicit one, as today)
  - `generate.configured(config: Mapping[bytes, TreeConfig], all_trees: Sequence[Tree]) -> dict[int, TreeConfig]` — only trees with a section, each `sets` passed through `with_implicit`. Raises `ValueError` as `tree_sets` does today (no tree / several trees for a root).
  - `generate.select_trees(all_trees: Sequence[Tree], configured: Collection[int], ids: Sequence[int]) -> list[Tree]`
  - `generate.merged_index(out: Path, entries: list[dict], live: Collection[int]) -> list`
  - `generate.read_tree(conn, tree: Tree, res: Resolution, config: TreeConfig, max_dirs: int)` (budget used in Task 3)

- [ ] **Step 1: Write failing tests in `tests/test_sets.py`**

  Helper text: `T = '[tree."/a".set.s]\ndirs = [{ match = ["*"], depth = 0 }]\n'`.
  - `test_load_reads_a_top_level_budget`: `"budget = 5\n" + T` → `load(f)[b"/a"].budget == 5`.
  - `test_load_tree_budget_overrides_the_top_level_one`: `'budget = 5\n[tree."/a"]\nbudget = 7\n' + T + '[tree."/b".set.s]\ndirs = [{ match = ["*"], depth = 0 }]\n'` → budgets `{b"/a": 7, b"/b": 5}`.
  - `test_load_rejects_missing_budget_and_names_both_places`: `T` alone → `ValueError` matching `"/a"`, `"budget"`, and the message names both the top-level and the tree-level place.
  - `test_load_rejects_bad_budgets` parametrized over `"0"`, `"-1"`, `"true"`, `"1.5"`, `'"10"'`, at top level and in a tree → `ValueError` matching `"'budget' must be an integer >= 1"`.
  - `test_load_rejects_a_file_without_trees` parametrized over `""`, `"budget = 5\n"`, `"budget = 5\n[tree]\n"` → `ValueError` matching `"no trees"`. Delete `test_load_accepts_a_file_without_trees`.
  - `test_load_rejects_a_tree_with_only_a_budget`: `'budget = 5\n[tree."/a"]\nbudget = 3\n'` → `ValueError` matching `"'set' must be a table of sets"` (today's message).
  - Extend the unknown-key tests: a top-level key other than `tree`/`budget`, and a tree key other than `set`/`budget`, are errors naming the key. Update every existing `load` test's text to carry `budget = 1\n`.

- [ ] **Step 2: Run** `venv/bin/pytest tests/test_sets.py -q` — expected: the new tests FAIL.

- [ ] **Step 3: Implement** `TreeConfig`, `_budget(where: str, value: object) -> int` (the `depth` checks: `int`, not `bool`, ≥ 1; message `"{where}: 'budget' must be an integer >= 1"`), and in `load`: allowed top-level keys `{"tree", "budget"}`, tree keys `{"set", "budget"}`; a missing/empty `tree` table raises `"{path}: no trees: add a [tree.\"/fs/path\".set.\"NAME\"] section"`; a tree without either budget raises `"{path}: tree '/a' has no budget: add budget = N to its [tree.\"/a\"] section, or at the top of the file for every tree"`. Update the module docstring (budget keys; who resolves it).

- [ ] **Step 4: Write failing tests in `tests/test_generate.py`**
  - Every sets-file text the tests write gets a top-level `budget = 100\n` (extend `_write` with `budget: int | None = 100` that prepends it; `None` writes the text as is).
  - `test_configured_goes_by_tree_root_and_adds_the_implicit_set` replaces `test_tree_sets_goes_by_tree_root...`: trees `/npx/user`, `/npx/user/vbrik`, `/sim`, sections for 1 and 3 → keys `{1, 3}`, names `["Top level", "s"]` each, budgets carried.
  - Keep the two `tree_sets` rejection tests, renamed for `configured`; drop the "without a section, sharing a root is harmless" assert's expectation to `{}`.
  - `test_select_trees_defaults_to_configured`: `select_trees([a, b, c], {1, 3}, []) == [a, c]`.
  - `test_select_trees_rejects_an_unconfigured_tree`: `select_trees([a, b], {1}, [2])` raises `ValueError` matching `r"tree 2 \(/npx/user/vbrik\) has no section in --sets"`; unknown ids still raise as in `test_select_trees_names_unknown_ids`.
  - `test_merged_index_keeps_only_live_configured_trees` replaces the `merged_index` test: old entries 1, 2, 3, live `{1, 2}`, new entry 2 → `[{id 1 old}, {id 2 new}]`.
  - `test_run_skips_trees_without_a_section` replaces `test_run_gives_trees_without_a_section_the_implicit_set`: no `tree-2.json` written, `trees.json` ids `[1]`; with `-v`, stderr has `"1 tree without a section in --sets skipped"`.
  - `test_empty_sets_file_is_an_error` replaces `test_empty_sets_file_is_valid`: `_write(tmp_path, "", budget=None)` → exit 1, stderr starts `"ERROR: "` and contains `"no trees"`; the DB is not connected (`monkeypatch` `connect` to `pytest.fail`).
  - `test_run_keeps_patterns_to_their_own_tree`: give `/npx/user/vbrik` its own section so its tree is still written.
  - Tests that used `_write(tmp_path, "")` for an unrelated reason (`test_unwritable_out...`, `test_database_errors...`) get a one-tree text instead.

- [ ] **Step 5: Run** `venv/bin/pytest tests/test_generate.py -q` — expected: the new tests FAIL.

- [ ] **Step 6: Implement** in `generate.py`: `configured` (replaces `tree_sets`), the new `select_trees` and `merged_index` signatures, `run` uses them (live = the configured ids: `configured` already rejects a section without a tree; `-v` note `"{n} tree(s) without a section in --sets skipped"` via `_count` when n > 0), `read_tree` takes the `TreeConfig` (passes `config.sets`; keeps passing `args.max_dirs` until Task 3). Rewrite `DESCRIPTION`'s sets paragraph and `--sets` help: the file is required and must name at least one tree; only its trees are generated; a top-level `budget` or each tree's own sets how many dirs a tree stores.

- [ ] **Step 7: Run** `venv/bin/pytest -q && venv/bin/ruff format src tests && venv/bin/ruff check src tests` — expected: all pass, clean.

- [ ] **Step 8: Commit** `src/dirhistory/sets.py src/dirhistory/generate.py tests/test_sets.py tests/test_generate.py` — "Sets file: per-tree budget; generate only configured trees".

---

### Task 2: The cut

**Files:**
- Modify: `src/dirhistory/sets.py` (new `Cut`, `cut`, `level`)
- Test: `tests/test_sets.py`

**Interfaces:**
- Produces:
  - `sets.level(path: bytes) -> int` — components; `b"."` is 0 (reuse in `Matcher.add`'s length computation).
  - `sets.Cut` — `@dataclass(frozen=True, slots=True)`: `kept: dict[bytes, int]` (the kept paths of STORED with their depth left), `pruned: list[bytes]` (sorted), `level: int | None` (the partial level; `None`: nothing left out).
  - `sets.cut(stored: Mapping[bytes, int], budget: int) -> Cut` — STORED as `Matcher.stored()` returns it.

The algorithm (the spec's "The cut"), since tests alone leave the unit rule open:

```python
by_level = defaultdict(list)          # level -> sorted candidate paths
kept, left = {}, budget
for lvl in sorted(by_level):
    paths = by_level[lvl]
    if len(paths) <= left: keep all; left -= len(paths); continue
    # units: a candidate whose parent is a candidate goes with its siblings
    units = group paths by (parent if parent in stored else the path itself),
            ordered by each unit's first path
    for unit in units:
        if len(unit) > left: break
        keep unit; left -= len(unit)
    partial = lvl; break
pruned = sorted({parent(p) for p in stored.keys() - kept.keys()
                 if p != ROOT and stored.get(parent(p), 0) >= 1 and parent(p) in kept})
```

(`parent` and `ROOT` move from `history.py` to `sets.py`, re-exported by `history`, so `sets` does not import `history`.)

- [ ] **Step 1: Write failing tests** (`stored` dicts written out; `cut(stored, budget)`):
  - `test_cut_keeps_everything_within_budget`: `{a:1, a/x:0, b:0}`, budget 3 → `kept == stored`, `pruned == []`, `level is None`.
  - `test_cut_exact_budget_is_no_cut`: same, budget 3; and a level boundary: `{a:1, b:1, a/x:0, b/y:0}`, budget 4 → no cut.
  - `test_cut_stops_between_levels`: `{a:1, b:1, a/x:0, a/y:0, b/z:0}`, budget 2 → kept `{a, b}`, level 2, pruned `[a, b]`.
  - `test_cut_fills_a_level_by_whole_parents`: `{a:1, b:1, c:1, a/x:0, b/y:0, b/z:0, c/w:0}`, budget 5 → kept `a, b, c, a/x` (unit `b`'s 2 do not fit after `a`'s 1: left 1), pruned `[b, c]`, level 2. Stops at `b`: `c/w` (fits) is not kept.
  - `test_cut_first_unit_too_big_keeps_none_of_the_level`: `{a:1, a/x:0, a/y:0}`, budget 2 → kept `{a}`, pruned `[a]`.
  - `test_cut_fills_unparented_set_dirs_one_by_one`: `{a:0, b:0, c:0}` (no stored parent), budget 2 → kept `{a, b}`, pruned `[]`, level 1.
  - `test_cut_does_not_prune_a_dir_stored_at_depth_0`: `{q:0, q/r:0, a:0}`, budget 2 → kept `{a, q}`, pruned `[]`.
  - `test_cut_never_prunes_dirs_without_candidate_subdirs`: `{a:2, a/x:1}` (max_depth stops at level 2), budget 2 → no cut, `pruned == []` (`a/x` has depth left but no candidate subdirs); budget 1 → kept `{a}`, pruned `[a]`.
  - `test_cut_root_is_level_0`: `{.:1, a:0, b:0}`, budget 2 → kept `{.}`, pruned `[.]`, level 1.
  - `test_cut_orders_by_bytes`: names `b"caf\xe9"`, `b"cafe"`, `b"Z"` at level 1, unparented, budget 2 → kept `{Z, cafe}`.
  - `test_level`: `level(b".") == 0`, `level(b"a/b") == 2`.

- [ ] **Step 2: Run** `venv/bin/pytest tests/test_sets.py -q -k "cut or level"` — expected: FAIL (no `cut`).

- [ ] **Step 3: Implement** `level`, `Cut`, `cut` (docstring: the rule, why whole parents — a partly stored dir is neither splittable nor marked — and why pruning needs stored depth ≥ 1).

- [ ] **Step 4: Run** `venv/bin/pytest -q && venv/bin/ruff format src tests && venv/bin/ruff check src tests` — expected: pass, clean.

- [ ] **Step 5: Commit** `src/dirhistory/sets.py src/dirhistory/history.py tests/test_sets.py` — "Sets: cut stored dirs to a budget, breadth-first by whole parents".

---

### Task 3: Apply the budget; remove `--max-dirs`

**Files:**
- Modify: `src/dirhistory/history.py` (`summarize`, `TreeHistory`, module docstring)
- Modify: `src/dirhistory/sets.py` (`select`, `SetDirs`; delete `MAX_DIRS`, `BUSIEST_TOP`, `Busiest`, `busiest`, `TooManyDirs`, `Matcher.counts` and `_covered`, unused imports)
- Modify: `src/dirhistory/generate.py` (delete `--max-dirs`, `busiest_hint`, the `TooManyDirs` handler; `tree_document`, `set_warnings`, new `cut_warning`, `-v` line)
- Test: `tests/test_history.py`, `tests/test_sets.py`, `tests/test_generate.py`

**Interfaces:**
- Consumes: `sets.cut`, `sets.Cut`, `sets.TreeConfig` (Tasks 1–2).
- Produces:
  - `summarize(samples, changed, first, fetch, resolution=DEFAULT_RESOLUTION, sets=(), budget: int | None = None) -> TreeHistory` — `None`: no cut (library/test convenience; the CLI always passes one).
  - `TreeHistory` gains, after `split`: `pruned: list[bytes]`, `candidates: int` (dirs the sets cover), `cut_level: int | None` (`Cut.level`).
  - `SetDirs` gains `dropped: int = 0` (set dirs left out by the cut); `select(name, matches, kept: Container[bytes]) -> SetDirs` — `found` counts matches before the cut, `paths` only kept ones.
  - `generate.cut_warning(tree: Tree, history: TreeHistory, budget: int) -> str | None`
  - `generate.read_tree(conn, tree, res, config: TreeConfig)`

- [ ] **Step 1: Write failing tests in `tests/test_history.py`** (helper `run(..., budget=None)` replaces `cap`):
  - `test_budget_cuts_before_fetching_and_fetches_only_kept`: `first = [ROOT, a, a/x, a/y, b]`, set `("*", 1)`, budget 3; `fetch` records its argument. Level 1 `{a, b}` fits (left 1), unit `a`'s `{a/x, a/y}` does not → fetched `{a, b}`; `h.pruned == [b"a"]`, `h.split == []`, `h.candidates == 4`, `h.cut_level == 2`.
  - `test_budget_drops_set_dirs_below_the_cut`: sets `ds("Top", ("*", 0))`, `ds("Deep", ("*/*", 0))`, first `[ROOT, a, b, a/x, b/y]`, budget 3 → `Deep` set: `paths == [b"a/x"]`, `found == [2]`, `dropped == 1`.
  - `test_no_budget_keeps_everything`: replaces both `test_too_many_stored_dirs_*` tests.
  - `test_stored_count_is_distinct_paths_across_sets`: `budget=2`, unchanged otherwise.
  - Existing asserts on `TreeHistory` stay; add `h.pruned == []` and `h.cut_level is None` to `test_root_set_dir_splits`.
- [ ] **Step 2: Update `tests/test_sets.py`**: delete the `TooManyDirs`, `busiest`, `counts` tests and imports; `test_select_keeps_config_order_and_first_occurrence` → `select("s", [[b, c], [a, b]], {a, b, c}) == SetDirs("s", [b, c, a], [2, 2], 0)`; new `test_select_leaves_out_cut_paths`: kept `{b}` → `paths == [b"b"]`, `found == [2, 2]`, `dropped == 2`.
- [ ] **Step 3: Write failing tests in `tests/test_generate.py`**:
  - `test_tree_document_round_trip`: `HISTORY` gains `pruned=[b"caf\xe9"]`, `candidates=3`, `cut_level=None` → `loaded["pruned"] == ["caf\udce9"]`.
  - `test_run_cuts_a_tree_to_its_budget_and_warns`: tree `/exp` with `[(ROOT, 9), (a, 5), (a/x, 3), (a/y, 1), (b, 4)]`, set `{ match = ["*"], depth = 1 }`, budget line `budget = 3` → exit 0; doc `pruned == ["a"]`, `split == []`, series keys `{"a", "b"}`; stderr exactly `"WARNING: tree 1 (/exp): budget 3 reached: stored 2 of 4 directories, every level above depth 2 in full; 1 directory whose subdirectories were left out is marked * in the page. Raise the tree's budget in the sets file to store more.\n"` (stored is `len(series)`; singular/plural via `_count`).
  - `test_run_warns_of_set_dirs_below_the_cut`: a second set `{ match = ["*/*"], depth = 0 }` → stderr also has `"WARNING: tree 1 (/exp): set 'Deep': 2 of its 2 dirs are below the budget's cut and left out\n"`, and no "matches no directory" line for it.
  - `test_run_does_not_warn_at_exact_budget`: same tree, budget 4 → no `WARNING`, `pruned == []`.
  - `test_removed_options_are_gone`: add `"--max-dirs"` to the parametrize list. Delete `test_max_dirs_must_be_positive`, `test_run_fails_when_stored_dirs_exceed_the_cap`, both `busiest_hint` tests, `test_too_many_dirs_error_names_the_busiest_dirs`.
  - `test_verbose_reports_pruned`: `-v` line ends `", 1 pruned, X.X s"` (match with a regex on the number of seconds).
- [ ] **Step 4: Run** `venv/bin/pytest -q` — expected: the new tests FAIL.
- [ ] **Step 5: Implement**: `summarize` calls `cut(stored, budget)` when `budget` is not `None` (else keeps all), fetches `set(kept)`, computes `split` over `kept`, selects sets with `kept`; docstring replaces the MAX_DIRS sentence with the cut. `history.py` module docstring: stored dirs are the covered ones within the budget. `generate`: `read_tree(conn, tree, res, config)` passes `config.sets, config.budget`; `tree_document` writes `"pruned"` after `"split"`; `cut_warning` returns the message above when `history.cut_level is not None`; `set_warnings` also yields the dropped-dirs line per set with `dropped > 0` (the implicit set too: dropped dirs are the budget's doing, not a pattern mistake); `-v` line inserts `", {len(history.pruned)} pruned"` before the time. Delete the removed names everywhere (`grep -rn "max_dirs\|MAX_DIRS\|TooManyDirs\|busiest\|counts()" src tests` → nothing).
- [ ] **Step 6: Run** `venv/bin/pytest -q && venv/bin/ruff format src tests && venv/bin/ruff check src tests` — expected: pass, clean.
- [ ] **Step 7: Commit** `src/dirhistory/history.py src/dirhistory/sets.py src/dirhistory/generate.py tests/test_history.py tests/test_sets.py tests/test_generate.py` — "Generate: cut trees to their budget, write pruned dirs; drop --max-dirs".

---

### Task 4: Mark pruned dirs in the page

**Files:**
- Modify: `web/js/model.js` (`normalize`: `pruned`; `stackBands`: band flag; exports `PRUNED_MARK`, `PRUNED_TITLE`, `markedLabel(band)`)
- Modify: `web/js/main.js` (legend name, table cell, chips, Trends card name and title, two notes)
- Modify: `web/js/charts/stackedArea.js` (series `name` uses the marked label)
- Modify: `web/index.html` (`<p id="pruned-note" class="muted" hidden>` after `#hidden`; `<p id="trends-pruned" class="muted" hidden>` after `#trends-hidden`)
- Test: `scripts/check_web.py` (new `check_pruned`, registered in the fixture checks list)

**Interfaces:**
- Consumes: data file `pruned` (Task 3).
- Produces: `normalize(raw).pruned: Set<string>` (empty for a file without the key); each `stackBands` band has `pruned: boolean` (`!band.own && data.pruned.has(band.dir)`); `markedLabel(band) -> string` (`band.label + PRUNED_MARK` if `band.pruned`). The mark never enters `band.label` (name sort, keys, URL use the plain label).

- [ ] **Step 1: Write `check_pruned(page, base)`** in `scripts/check_web.py`:
  - First on the plain fixture (no `pruned` key): `go(page, f"{base}#tree=1")`; no legend label ends with `*`; `#pruned-note` hidden.
  - Then serve an altered copy: `raw = json.loads((FIXTURE / "tree-1.json").read_text()); raw["pruned"] = ["Q"]; page.route("**/data/tree-1.json", lambda r: r.fulfill(json=raw))`; `go(page, f"{base}#tree=1")`.
  - `"Q*" in bands(page)`, and `A`, `Z` unmarked; the `Q*` label's `title` is `PRUNED_TITLE`; `#pruned-note` visible with text `"* Subdirectories not stored: the tree's budget ran out."`.
  - Sorting the legend by Dir (`#legend-head .sort[data-sort=name]`) gives the same order as the unmarked labels with `*` stripped.
  - Table first-column cells include `"Q*"`.
  - Hide `Q` (its `button.hide`) → chip text `"Q* ✕"`.
  - Trends (`#tree=1&view=trends`): `card(page, "Q*")` exists, its `.name` title starts with the absolute path and contains `PRUNED_TITLE`; `#trends-pruned` visible. With `set=Mixed` (no Q): `#trends-pruned` hidden.
  - Register it after `check_fixture` in `main`'s list.
- [ ] **Step 2: Run** the check command — expected: `check_pruned` problems (no marks yet), everything else as before.
- [ ] **Step 3: Implement** the model and render changes above. Notes: Stack note visible iff any of `model.bands` is pruned; Trends note iff any shown card's path is in `data.pruned`. The Trends card's `title` is `` `${absolute(path)} — ${PRUNED_TITLE}` `` for a pruned card. `hiddenName(key)` marks a dir key (not `X/.`) in `data.pruned`. `.label` elements of pruned bands get `title = PRUNED_TITLE` (a drillable band never is pruned, so its "Split …" title is untouched).
- [ ] **Step 4: Run** the check command — expected: `0 problems` for the fixture checks.
- [ ] **Step 5: Commit** `web/js/model.js web/js/main.js web/js/charts/stackedArea.js web/index.html scripts/check_web.py` — "Page: mark dirs the budget pruned with *".

---

### Task 5: Docs and deploy files

**Files:**
- Modify: `README.md`, `docs/design.md`, `docs/terms.md`, `deploy/sets.toml.example`, `deploy/dirhistory.service`
- Modify: `docs/superpowers/specs/2026-11-05-dir-budget-design.md` (Status line, after Task 6)

- [ ] **Step 1: README**: quick start writes a minimal sets file (`budget = 100000` plus one `[tree."/fs/path".set."Top"]` section) instead of `touch sets.toml`; the options sentence loses "directory limit"; replace the `--max-dirs` paragraph with: only the file's trees are generated, each needs a budget (top-level default, per-tree override), a tree over budget is stored breadth-first and saved partial, with pruned dirs marked `*`; an upgrade note: add `budget` to the live sets file before deploying, and drop `--max-dirs` from any script.
- [ ] **Step 2: docs/design.md**: Sets section — replace the `--max-dirs` bullet with the budget and the cut (levels whole, partial level by whole parents in path order, stop at the first misfit; pruned = would have been split; set dirs below the cut dropped with a warning); Top level is no longer "browsable with an empty file"; only configured trees run, `trees.json` keeps only configured trees. Data file table: `pruned` row. Risks: "Large depth makes large files" — the budget bounds a file; give bytes per dir from Task 6's run; "Lost trees" also covers trees removed from the sets file. Page section: the `*` mark and notes.
- [ ] **Step 3: docs/terms.md**: rows for budget, cut, partial level, pruned dir; update "depth (of a set dir)" and "stored dir" (within the budget).
- [ ] **Step 4: deploy/sets.toml.example**: top-level `budget = 100000` with a comment (required unless every tree sets its own; what the cut does), a `[tree."/data/projects"] budget = 500000` override, drop "an empty file is valid" and `--max-dirs`. **deploy/dirhistory.service**: comment — the sets file must name each tree to generate, with a budget.
- [ ] **Step 5: Check** every claim against the code (`dirhistory-generate --help`, `sets.load` errors); `grep -rn "max-dirs\|max_dirs\|empty file" README.md docs deploy src` → only historical specs/plans.
- [ ] **Step 6: Commit** `README.md docs/design.md docs/terms.md deploy/sets.toml.example deploy/dirhistory.service` — "Docs: per-tree budget, partial trees and pruned dirs".

---

### Task 6: Real-database run, measurements, report

Commit everything first (lessons learned: a long live run can strand work).

- [ ] **Step 1: Prepare** a copy of `gitignore/dw-watch-ceph1-admin.toml` at `$SCRATCH/sets-1m.toml` with `budget = 1000000` prepended (do not edit the original). `TRACKDIR_DB` from the session, with `?connect_timeout=10`.
- [ ] **Step 2: Run** `/usr/bin/time -v timeout 3600 venv/bin/dirhistory-generate --sets $SCRATCH/sets-1m.toml -v` (writes `web/data`). Expected: exit 0, one cut warning (tree 2), per-tree `-v` lines. Record wall time, max RSS.
- [ ] **Step 3: Check invariants** on each written file with a scratchpad script: every level shallower than the cut level has all of its candidates (compare with `current_dir_state` counts per level); every kept dir in `split` has all its stored-children; no `pruned` dir is in `split`; `len(series) <= budget`. Print only counts.
- [ ] **Step 4: Measure the page**: per tree, file size; with Playwright, the time from `goto` to legend rendered for `#tree=N`, and for a Depth 1 re-render. Then run the check command and record the real-tree results (failures from fixed waits are findings).
- [ ] **Step 5: Report** to the user: table per tree (candidates, stored, pruned, cut level, file MB, generate s, page load s, Depth-1 render s), max RSS, check_web outcome. Put bytes-per-dir into design.md's risk (Task 5 Step 2), propose AGENT-LESSONS-LEARNED entries for anything surprising, set the spec's Status to "implemented (2026-11-05)", and commit `docs/design.md docs/superpowers/specs/2026-11-05-dir-budget-design.md` (and `AGENT-LESSONS-LEARNED.md` if changed).
