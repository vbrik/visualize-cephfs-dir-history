# Sets drive both views: Stack and Trends — design

Status: draft for approval, 2026-10-23. Supersedes the unit-based Units view
(docs/design.md, "Stack own sizes, not rbytes", "Units: significant own-size
change", "Propagation lag") and the watch file
(`2026-10-22-watched-dirs-design.md`, "Config file").

## Intent

Units (dirs whose own size changed by ≥ `--threshold`) answer "what changed
anywhere, at any depth", but people look at the tree through chosen dirs at
chosen depths. The watch sets already name those dirs for the Watched view.
This change makes the sets the one way to choose what both views show:

- **Stack** view (was Units): a stacked area of the **rbytes** of a set's
  dirs, optionally split into their subdirs N levels down (**Depth**).
- **Trends** view (was Watched): one trend card per set dir, unchanged.

Units, own-size series, `--threshold` and `--max-units` go away entirely.

## Goals

- A required sets file (`--sets`) whose sets list dirs by pattern, each
  pattern group with a required `depth`: how many levels below each matched
  dir the generator stores, so the page can split and drill into it.
- An implicit set per tree, always present and first: every top-level dir,
  depth 0.
- The Stack view: Set, Depth and Top controls; bands are rbytes; the Top
  (default and max 64) bands by span are drawn, the rest are not;
  drilling and hiding stay.
- Removed from the page: Group by, Bands, Min peak, Rest of tree (control,
  band and the tooltip's "Rest of tree (not drawn)" line), Smaller units
  (band, legend and table row). No "Other" band replaces them: current use
  cases do not need one; it may come back later.
- A 64-color categorical palette whose first 8 are today's.

## Non-goals

- Editing sets in the browser.
- Guarding against sets that nest dirs (a dir and its own subdir in one
  set). Their bands overlap and the stack double-counts; that is the set
  author's choice.
- Choosing sane depths for the user: a large `depth` on a broad pattern can
  make a huge file. `--max-dirs` catches accidents only.

## Anti-goals

- A stack that silently loses bytes: within a split dir, the files directly
  in it get their own band. Bands past Top are not drawn, but the summary
  says how many are left out (*my call*), so the gap is never silent.
- Unchanged dirs missing from the chart: they are stored and shown like any
  other (as single numbers in the file, see Data files).
- Two meanings for one number: `depth` in the file and Depth on the page
  both count levels below a set dir, 0 being the dir itself.

## Terms (replace in docs/terms.md)

| Term | Meaning |
|---|---|
| set | A named list of dir groups of one tree, in its `[tree."/fs/path".set."NAME"]` section of the `--sets` file, plus the implicit **Top level** set. Both views show one set at a time ("Set"). |
| dir group | One entry of a set's `dirs`: `{ match = [patterns], depth = N }`. |
| set dir | A dir that a pattern of one of the set's groups matches. |
| depth (of a set dir) | How many levels below the set dir the data file stores, so the page can split or drill into it: 0 = the dir only, 1 = also its subdirs, … The largest `depth` of any group, in any set, matching the dir; limited by the tree's `max_depth`. |
| stored dir | A dir whose series the tree file holds: a set dir, or a dir within its depth below one. |
| Depth (page) | How many levels below the set dirs (or the drilled-into dir) the Stack view splits; 0 = the dirs themselves. A dir not in the file's `split` list (not all of its subdirs stored) stays whole. |
| Top (page) | How many bands the Stack view draws, the largest spans first (default and max 64). |
| own-files band | `X/ bytes`: in a split dir X, the bytes of the files directly in X: rbytes(X) − Σ rbytes(X's subdirs), at least 0. |
| Stack view / Trends view | The page's two views (`view=` in the URL hash). |

Removed: unit, leaf unit, threshold, own size (replaced by own-files band),
group, Smaller units, Rest of tree, ghost unit, watch set, watch pattern,
watched dir.

## The sets file

`--sets FILE` is required; an empty file is valid (every tree then has only
the implicit set). Example (`deploy/sets.toml.example`, replacing
`watch.toml.example`):

```toml
[tree."/exp".set."set-1"]
dirs = [
  { match = ["a", "b", "c"], depth = 0 },       # the dirs only
  { match = ["x*", "y", "zq*"], depth = 3 },    # down to 3 levels below each
]
```

- Keys: `dirs` (required, a non-empty list of groups); per group `match`
  (non-empty list of patterns, the existing pattern syntax: relative to the
  tree root, wildcards per component, `.` for the root, no `**`) and
  `depth` (required integer ≥ 0). Unknown keys are errors.
- `watch = [...]` (the old format) is an error whose message shows the new
  one.
- A dir matched by several groups, in one set or several, is stored to the
  largest `depth`. The page splits and drills by what is stored, not by
  the current set's own depths: if set B stores `X` 3 levels deep, set A,
  which gives `X` depth 1, can also go 3 levels deep. So `depth` decides
  what gets stored, and the file need not record it per set.
- Tree-section rules are unchanged (named by the tree root, assumed unique;
  a section naming no tree or several trees is an error; a pattern matching
  nothing is a warning).
- The implicit set: name **Top level**, `match = ["*"], depth = 0`, first in
  every tree's Set menu. *My call:* a set in the file with the same name
  replaces it in place (still first), e.g. to give top-level dirs depth 2.
  The implicit set is added when the file is read (`sets.py`), so the rest
  of the code sees one list of sets.

## Generator

- Module `watch.py` becomes `sets.py` (WatchSet → DirSet, etc.).
- `history.summarize` loses unit selection, own sizes, the running median,
  `threshold` and `limit`. It matches each set's patterns in the existing
  single pass over the first sample (plus the changed paths, for dirs made
  later), then adds each set dir's descendants down to its depth (and the
  tree's `max_depth`). Those are the **stored dirs**: the union over all
  sets.
- Stored dirs that changed get their series from the fetch (as watched dirs
  do now); the rest are constant at their first-sample value.
- Before fetching, more than `--max-dirs` stored dirs in a tree is an error
  naming the sets and their counts. *My call:* default 20 000, per tree,
  since all sets share one file. Live DB numbers for scale: tree 4's
  implicit set alone is 2 994 dirs; `*` at depth 1 is 379 dirs on tree 2 and
  17.3 k on tree 4; `*` at depth 2 is 18.6 k on tree 2 and 234 k on tree 4.
- CLI: `--sets FILE` (required) and `--max-dirs N` replace `--watch` and
  `--max-watched`; `--threshold` and `--max-units` are removed. `--min-bucket`
  help speaks of own-files bands dipping instead of ghost units. Verbose
  stats: points, stored dirs, sets, time.

## Data files

`trees.json` entries: `{id, label, detail, file, last_sample, generated,
sets}`, `sets` being `[{name, dirs}]` (`dirs` = number of set dirs). `units`
is gone.

`tree-<id>.json`:

| Key | Content |
|---|---|
| `generated`, `tree`, `times` | As now. |
| `rbytes` | `{path: series}` for every stored dir. A series is a list (bytes or null per point) if it changed, else one number or null: its constant value. |
| `sets` | In file order, the implicit set first: `[{name, paths}]`, set dirs in pattern order, by path within a pattern. |
| `split` | The stored dirs whose subdirs are all stored (each less than its depth below a set dir, and above `max_depth`), sorted. Only these can be split or drilled into. |

`threshold`, `units` and ancestors-only entries (ROOT, ancestors of set
dirs) are gone: nothing shows them. A data file of this format is written
by this version only; the page needs no fallback for old files (the next
generator run replaces them).

Why one number for unchanged dirs: on the live DB, 99.5 % of the dirs these
sets store never changed (tree 1, `*` at depth 3: 10 622 of 10 678). At 400
points, full lists would make that file ~50 MB; with scalars it is a few
hundred KB.

## Stack view

State (URL hash): `set`, `depth`, `top`, `in`, `hide`; Reset restores
`depth`, `top`, `in`, `hide`.

- **Bands.** Start from the set dirs, or from `in` when drilled. Expand each
  Depth levels: a dir X less than Depth below the shown dir, with stored
  subdirs, becomes `X/ bytes` plus one band per stored subdir, recursively;
  any other dir stays one band of its rbytes. Absent counts as 0. "With
  stored subdirs" means listed in `split`, not merely having some subdir
  in `rbytes`: set B's dir `a/sub` is stored even when set A gives `a`
  depth 0, and splitting `a` on that would fold `a/sub`'s siblings into
  `a/ bytes` (*my call*).
- **Depth control:** a number field, default 0, max = how many levels are
  stored below the shown dirs (set dirs, or `in`). Relative to the shown
  dir, like today's Group by is relative to `in`.
- **Top control:** a number field, default 64, 1–64 (the palette's size).
  Bands rank by span (max − min) over the shown range, ties by key; the
  first Top are drawn, the rest are not drawn and not summed anywhere;
  the summary line says how many are left out. Hidden bands are not
  ranked.
- **Drilling.** Clicking a band (legend or chart) whose dir is in `split`
  sets `in` to it and Depth to 1. Breadcrumbs: `Set NAME` / the set
  dir (its path relative to the tree root, as one crumb) / each component
  below it. *My call:* `in` must be a set dir of the current set or a stored
  dir below one; otherwise it is ignored. Changing the set or tree clears
  `in`.
- **Hide.** Unchanged in spirit: hiding a dir band removes that dir's bytes
  at every Depth (its subtree); hiding `X/ bytes` removes only those files.
  Chips bring them back. Tree change clears hides (as now).
- **Labels:** paths relative to the shown root (the tree root when not
  drilled, else `in`); own-files bands `X/ bytes`.
- **Tooltip:** rows per band and Total shown; no Rest of tree line.
- **Summary line:** the shown dir (or set), its total in the shown bands,
  the number of set dirs, how many bands are past Top, generation time.
  No unit count anywhere (also not in the Tree menu).

## Trends view

Unchanged, except: cards are the current set's set dirs (not their stored
descendants); it is named Trends (`view=trends`); the Set control and `set=`
are shared with the Stack view. Clicking a card opens the Stack view with
`in` = that dir and Depth 1 (0 if it is not in `split`).

## Palette

64 categorical slots, light and dark (`--series-1` … `--series-64`); the
first 8 are today's. The builder generates the other 56 with the dataviz
skill's method and validator: same lightness/chroma band as the 8, hues and
lightness spread so that colors near each other in slot order differ
clearly. 64 colors cannot all be pairwise distinct; bandColors already keeps
survivors in their slots and gives newcomers free ones, so neighbors in the
stack usually differ. With at most 64 bands (Top's max) the palette never
has to cycle; `--band-smaller` and `--band-rest` go.
conventions.md changes "8-slot palette, never cycle" to this.

## Propagation lag

CephFS can update a child's rbytes before its parent's within a sample, so
an own-files band can dip (or go negative, clamped to 0) for a point. With
units gone there is no running median. Measured on the live DB (2 days of
hourly points): the largest such artifact in any own-files band is 0.25 GiB;
8.6 GiB was seen earlier at finer sampling. It only affects own-files bands
and can lift one into the Top by a single-point spike. *My call:* clamp
at 0, keep `--min-bucket` 1h, document as a risk in design.md; no smoothing.

## Docs and checks to update

README (intro, controls table, CLI, deploy steps), docs/design.md (rewrite
Decisions, Page, Data files, Risks), docs/terms.md, docs/conventions.md
(palette), `deploy/dirhistory.service` (`--sets`), `deploy/sets.toml.example`,
a superseded note in `2026-10-22-watched-dirs-design.md`, tests
(`test_history.py`, `test_generate.py`, `test_watch.py` → `test_sets.py`),
`tests/data/web` fixtures, and `scripts/check_web.py` (fixture checks for
depth splitting, own-files bands, Top cut-off, drilling (including
past the current set's own depth), hide, set
switching; real-data checks per tree at Depth 0 and 1).
