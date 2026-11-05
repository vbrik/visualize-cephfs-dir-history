# Directory budget: partial trees instead of failing — design

Status: implemented (2026-11-05).

## Intent

`dirhistory-generate` stores, per tree, every directory its sets cover (a
set dir, or one within its `depth` below one). More than `--max-dirs`
(100 000) is an error. On the real database the operator's sets cover far
more than that, so the run fails:

| Tree | max_depth | Covered dirs |
|---|---|---|
| 1 | 5 | 227 k (10.7 k through depth 4) |
| 2 | 5 | 1.94 M (72 k through depth 4, 1.87 M at depth 5) |
| 3 | 7 | 855 k (557 k through depth 6) |
| 4 | 3 | 17 k |

The operator wants each tree generated as deep as a **budget** of stored
dirs allows, breadth-first from the tree root, and the partial tree saved,
instead of an error. The page marks the dirs where the budget cut the tree,
so a reader knows why a dir cannot be split or drilled into.

## Goals

- A per-tree **budget**: the most dirs a tree's file stores. Set only in the
  sets file: a top-level `budget = N` (the default for every tree), which a
  tree's own `budget = N` overrides. A tree with neither is an error.
- A tree whose sets cover more dirs than its budget is **cut**: shallower
  levels are stored completely before any deeper dir is (details below).
  The partial tree is written as usual, and a `WARNING:` says so.
- A **pruned dir** is a stored dir whose subdirs the cut left out. The data
  file lists them (`pruned`); the page shows their names with a `*`.
- Only trees with a section in the sets file are generated.

## Non-goals

- Choosing which dirs to keep by size, growth, or anything but depth and
  path. The operator: which dirs of the partial level are kept "doesn't
  matter", as long as every shallower level is complete.
- A budget per set, or on the command line.
- Deleting data files of trees no longer configured (as today for trees
  removed from the database: see design.md's "Lost trees").

## Anti-goals

- A dir of a shallower level dropped while a deeper one is kept.
- A partly stored dir: one with some of its subdirs stored and not others.
  It would be neither splittable (not all subdirs stored) nor marked.
- A tree failing because it is too big. Being over budget is a warning.

## Decisions

### Removed

The fail-on-too-many mode goes entirely (the operator's choice): the
`--max-dirs` option, `sets.MAX_DIRS`, `sets.TooManyDirs`, `sets.busiest`,
`sets.BUSIEST_TOP`, `generate.busiest_hint`, and their tests and docs. The
busiest-dirs hint only served to fix a failing run. A unit or script still
passing `--max-dirs` now fails with argparse's "unrecognized arguments";
`deploy/dirhistory.service` does not pass it.

### Sets file

- Top-level keys: `tree` (required) and `budget` (optional). Any other is an
  error, as today.
- A tree section's keys: `set` (required, as today) and `budget` (optional).
- `budget` is an integer ≥ 1; a bool (`budget = true`), a float, a string or
  0 is an error naming where it is, as `depth` errors do.
- Errors, all from `sets.load`, naming the file:
  - no `tree` table, or an empty one (an empty file is now an error);
  - a tree section without `set`, e.g. only `budget` (today's error);
  - a tree with no `budget` of its own and no top-level `budget`. The
    message names the tree and says to add either.
- `sets.load` resolves each tree's budget, so it returns per tree root its
  budget and its sets (e.g. a small frozen dataclass `TreeConfig(budget,
  sets)`; the shape is the builder's). The implicit set is still added
  later (`with_implicit`).

### Which trees are generated

- Only configured trees: those with a section in the sets file. A section
  whose root names no tree, or several, is an error, as today.
- Trees in the database without a section are skipped silently; `-v` notes
  how many (*my call*).
- `--tree ID` still narrows the run. An ID with no tree is an error (as
  today); an ID whose tree has no section is an error too (*my call*).
- `trees.json` keeps an old entry only for a tree that still exists **and**
  is configured, so a tree removed from the sets file drops out of the index
  on its next run (*my call*). Its `tree-ID.json` stays on disk.

### The cut

Done in `history.summarize` where the `max_dirs` check is today: after
matching, before any series is fetched, so the query fetches only kept dirs.

- **Candidates**: the dirs the sets cover (`Matcher.stored()`), as today.
- A dir's **level** is its number of path components (the tree root is 0).
- Levels are taken shallowest first. A level whose candidates all fit in
  what is left of the budget is kept whole. The first level that does not
  fit is the **partial level**; no candidate deeper than it is kept.
- The partial level is filled by **units**, in order of each unit's first
  path (byte order). A candidate whose parent is a candidate belongs to the
  unit of all its parent's candidate subdirs; every other candidate (a set
  dir whose parent is not stored) is a unit of its own. A unit is kept
  whole if it fits in what is left; at the first unit that does not fit,
  filling stops (*my call*: wastes at most one unit's worth of budget, and
  "everything up to this path" is easy to reason about). So a stored dir has
  either all its candidate subdirs stored or none.
- A level that fits exactly is kept whole; if no candidate is left out, the
  tree is not cut.
- **Pruned**: the kept dirs that would have been split without the cut but
  are not: stored with at least one level below them (`Matcher.stored()`
  value ≥ 1) and with their candidate subdirs left out (*my call*: the mark
  says why a dir cannot be split, so a dir that never could is not marked).
  A dir stored at depth 0 whose subdir is a set dir of its own, left out by
  the cut, is not pruned; nor is a dir with no candidate subdirs (a set's
  `depth` reached, or the tree's `max_depth`).
- `split` is computed over the kept dirs, as today. A pruned dir is never in
  `split` (none of its subdirs is kept), so the page already cannot split or
  drill into it.
- A set dir below the cut is left out of its set's `paths`. The "pattern
  matches no directory" warning still counts matches before the cut, so a
  pattern whose matches were all cut does not claim to match nothing.

Example: budget 1 000 000 on tree 2. Levels 0–4 (72 050 dirs) are kept
whole. Level 5 (1.87 M) is the partial level: level-4 dirs, in path order,
get all their subdirs while they fit. Those that do not are pruned.

`summarize` returns, besides what it returns today, what the cut did: the
pruned dirs (sorted), how many candidates there were, and the deepest
complete level (the exact shape is the builder's).

### Messages (stderr)

- Per cut tree, once:
  `WARNING: tree 2 (/data/ana): budget 1000000 reached: stored 999 870 of
  1 941 511 directories, every level through depth 4; 25 012 dirs whose
  subdirectories were left out are marked * in the page. Raise the tree's
  budget in the sets file to store more.`
  (Numbers plain, without separators, as the other messages print them;
  wording the builder's, content as above.)
- Per set that lost set dirs to the cut:
  `WARNING: tree 2 (/x): set 'S': 12 of its 40 dirs are below the budget's
  cut and left out.`
- `-v`'s per-tree line adds the pruned count.

### Data file

- New key `pruned`: the pruned dirs, sorted, `os.fsdecode`d as other paths.
  Always present (`[]` if the tree was not cut), as `split` is.
- `trees.json` is unchanged.
- The page reads a file without `pruned` (written before this change) as
  nothing pruned.

### Page

- The model loads `pruned` as a Set, like `split`. A dir band (not an
  own-files band) of a pruned dir gets a `pruned` flag.
- Wherever a pruned dir's name is shown it ends in `*`: the Stack legend,
  tooltip and table, and the Trends card name. The mark is display-only: it
  is not part of the label used for sorting by name, hit-testing or the URL
  (*my call*).
- A pruned name has a `title`: "Subdirectories not stored: the tree's
  budget ran out" (*my call*). The Stack legend and the Trends view show a
  one-line note with the same meaning when any listed band or shown card is
  pruned, and none otherwise (*my call*).
- The drill path (crumbs) never holds a pruned dir: only `split` dirs can
  be drilled into.
- A Trends card's name already has a `title` (its absolute path); a pruned
  card's title adds the note to it. Hidden-band chips get the `*` too, if
  they show a dir's name (*my call*).

### Docs

- `README.md`: the sets file now needs a budget; no `--max-dirs`; only
  configured trees are generated; an empty file is invalid. The quick start
  (`touch sets.toml`) changes to a file with a budget and a tree section.
- `docs/design.md`: Sets section (budget, the cut, which trees, Top level is
  no longer "browsable with an empty file"), the data file table (`pruned`),
  the risk "Large depth makes large files" (the budget now bounds a file;
  size per dir from the real run below).
- `docs/terms.md`: budget, cut, partial level, pruned dir.
- `deploy/sets.toml.example`: a top-level `budget`, one per-tree override,
  no "empty file is valid".
- `deploy/dirhistory.service`: its comment's "an empty file gives each tree
  only the implicit Top level set".
- `generate.py`'s `--help` (DESCRIPTION and `--sets` help).

### Tests

Unit tests, no live database:

- The cut: no cut when everything fits; a budget equal to a level's
  cumulative size (no cut); a cut between levels (the next level has a unit
  that does not fit first); a cut mid-level, stopping at the first unit
  that does not fit; a first unit larger than what is left (nothing of that
  level kept); set dirs without stored parents as single-dir units; a set
  dir below the cut left out of its set; `pruned` excludes leaves at a set's
  depth and at max_depth; `split` never holds a pruned dir; only kept dirs
  are fetched.
- The sets file: top-level and per-tree budget, override, missing budget,
  bad values (0, negative, bool, float, string), unknown keys, empty file,
  no trees, a tree with only `budget`.
- The generator: only configured trees are written; `--tree` of an
  unconfigured tree is an error; the index drops an unconfigured tree; the
  cut and set warnings; `pruned` in the file; `--max-dirs` is gone.
- `scripts/check_web.py`: a fixture tree with `pruned` (in
  `tests/data/web/`) shows `*` on the pruned band and card, and the note;
  a file without `pruned` loads.

### Verification on the real database

- With a copy of the operator's sets file (`gitignore/dw-watch-ceph1-admin.toml`)
  in the scratchpad, given `budget = 1000000` (the operator's file is not
  edited), generate all trees into `web/data` (gitignored; what
  `check_web.py` reads) under `timeout`, after committing tested work.
  Expect several GB of generator memory at 1 M dirs.
- Deploying needs a `budget` in the live sets file first, or every run
  fails: the README's upgrade note says so.
- Report per tree: stored and candidate dirs, pruned dirs, file size, run
  time; check the cut invariants on the real files (every level through the
  deepest complete one is whole; every kept dir has all or none of its
  candidate subdirs).
- Done means: unit tests and `scripts/check_web.py`'s fixture checks pass.
  Its real-data checks (every tree in `web/data`) wait a fixed `SETTLE_MS`
  per step, so they may fail on the large real files from slowness alone:
  they are run, but not required to pass (the operator's choice). Instead,
  measure and report, per real tree, the file size, the page's load time
  and a Stack render's time. Reworking the real-data checks to wait for
  readiness, or making the page faster, is separate work.
- Real paths stay out of git: commit messages, fixtures and docs use
  anonymized paths.

## Left to the builder

- The shapes of `TreeConfig` and of `summarize`'s cut result.
- Exact message wording, within the content above.
- How the page places the `*` and the note, within the rules above.
