# Watched directories view (design 2b) — design

Status: draft for approval, 2026-10-22.
Source: view "2b Small multiples" in `docs/Cephfs_Directory_Growth_design_example.html`
(a bundled page; its template and logic decode from the `__bundler/template`
script tag). The view is renamed: "small multiples" names the chart form, not
what the view is for.

## Intent

The Units view (today's page, design 2a) stacks units, which can be deep
(`IceCube/2026/filtered/PFFilt/1016`). Seeing one directory's shape there means
hiding every other band, and the directories people actually track sit at a
chosen depth (`IceCube/2026/filtered`), usually not units at all.

The Watched view shows a fixed, admin-chosen list of directories, one **trend
card** each: its size over the shown time range, on the card's own scale, so
its shape (steady climb, cleanup cliff, slow decline) is readable at a glance.
The list comes from a config file read by `dirhistory-generate`, not from the
web page.

## Goals

- A config file of watch patterns; the generator writes the matched
  directories' rbytes series into the tree files.
- A second page view, switched at the top, showing a grid of trend cards in
  config order, with a time-range slider shared with the Units view.
  *(Superseded 2026-10-23: see docs/design.md, Page. Cards sort by span by
  default; unchanged ones are hidden.)*
- Clicking a card opens the Units view drilled into that directory.

## Non-goals

- Editing the watch list in the browser.
- Sorting or filtering cards on the page. *(Superseded 2026-10-23: see docs/design.md, Page.)*
- Recursive patterns (`**`).
- Views 2c and 2d.

## Anti-goals

- Cards that let a tiny wiggle pass for a cliff: every card prints its scale's
  bottom and top.
- Unit controls (Group by, Bands, Min peak, Rest of tree, hide) shown in the
  Watched view as if they applied to it.
- Mixing up rbytes and own size: everything else on the page is own size;
  trend cards are whole-directory sizes (rbytes) and must say so.
- A slow run caused by the watch list: matching must stay inside the existing
  single pass over the first sample.

## Terms (add to docs/terms.md)

| Term | Meaning |
|---|---|
| watch pattern | An absolute path pattern in the `--watch` file, e.g. `/npx/ana/IceCube/*/filtered`. |
| watched dir | A directory of a tree that a watch pattern matches. |
| trend card | One watched dir's card in the Watched view: size, change and a sparkline of its rbytes. |
| Units view / Watched view | The page's two views (`view=` in the URL hash). |

## Config file

TOML, read with `tomllib` (no new dependency), passed as
`dirhistory-generate --watch FILE`. Without `--watch` no tree has watched dirs.

```toml
watch = [
  "/npx/ana/IceCube/*/filtered",
  "/npx/ana/LE",
  "/sim/IceCube/*",
]
```

- The only key is `watch`, a list of strings. Any other top-level key, a value
  that is not a list of strings, or a TOML syntax error is an `ERROR:` with a
  non-zero exit before the database is touched.
- A pattern is an absolute path, matched per component against a directory's
  absolute path (`tree.fs_path` joined with its path relative to ROOT; ROOT
  itself is `fs_path`). Components use shell wildcards (`*`, `?`, `[...]`),
  matched with `fnmatch.fnmatchcase` on bytes (pattern through
  `os.fsencode`), so `*` never crosses `/`. Component counts must be equal.
- A `--watch` file that does not exist or cannot be read is an `ERROR:`, like
  any other config problem. The README's Deploy step says to create it (an
  empty `watch = []` is valid) before enabling the updated unit.
  *(Builder-check call.)*
- Rejected with `ERROR:`: a relative pattern, an empty component (`//`, or a
  trailing `/`), a `.` or `..` component, a `**` component.
- Wildcards may appear in the tree part too (`/npx/*/IceCube` applies to every
  tree whose fs_path matches `/npx/*`). A pattern applies to a tree when its
  first components match all of `fs_path`'s components. Two trees with the
  same fs_path (different clusters) both get it.
- Non-UTF-8 directory names can only be matched through a wildcard (TOML
  strings are Unicode).

### Matching and order

- Candidates are every path of the tree that existed at any point: the first
  sample's paths plus the changed paths. A dir deleted later stays watched;
  its series turns null (absent) after the deletion.
- Matching happens inside the existing single pass over the first sample
  (`summarize`'s `first` stream, up to ~2 M rows, read once by `_children`);
  prefilter by component count before calling fnmatch. Changed paths are
  matched separately (they are already in memory).
- Order: config order; within one pattern, by path (bytes); a dir matched by
  several patterns appears once, at its first pattern.
- Cap: `--max-watched N` (default 100) per tree, applied after ordering. When
  it cuts dirs, a `WARNING:` names the tree and how many were dropped.
- `WARNING:` for a pattern that applies to at least one tree selected by
  `--tree` (all trees by default) but matches no dir in any of them, and for a
  pattern that applies to no selected tree only when `--tree` was not given
  (it is then likely a typo). One warning per pattern per run.

### Generator changes

- `history.summarize` takes the patterns (or a matcher) and returns the
  watched paths in `TreeHistory.watched: list[bytes]`.
- Watched paths must be added to the set whose rows are fetched (`wanted`):
  a watched dir with no changed descendants is otherwise never fetched and
  gets no series. Its single first-sample row is enough; `_replay` carries it
  forward.
- Watched paths and their ancestors must be added to `wanted` and to the
  kept `rbytes` keys (today ROOT, units and their ancestors), or their series
  is computed and dropped. Ancestors are needed because a card's click-through
  puts the dir in the Units view's crumbs, and every crumb must be a valid
  `in` (main.js falls back to ROOT for a path without rbytes). At most
  `max_depth` extra paths per watched dir. *(Builder-check call.)*
- Watch-pattern parsing and validation live in `generate.py` (or a small
  `watch.py` if that reads better); matching is pure and lives where
  `summarize` can call it. Both get pytest tests.

## Data files

Tree file, new key:

| Key | Content |
|---|---|
| `watched` | Watched dirs' paths relative to ROOT, in display order (see above). Each has an `rbytes` entry. `[]` when none. |

`trees.json` entries get `watched`: the count, so the page knows whether any
tree has watched dirs without fetching tree files.

The `rbytes` description in docs/design.md changes to "ROOT, every unit,
every watched dir, and every ancestor of these".

## Page

### View switch and state

- A `View` segmented control (`Units | Watched`) next to Tree. URL hash key
  `view`, omitted for the default `units`; `view=watched` otherwise.
- The control is hidden when no tree in `trees.json` has watched dirs; then a
  `view=watched` hash renders the Units view.
- For a tree with no watched dirs, the Watched view shows a one-line note
  ("No watched directories in this tree. See `dirhistory-generate --watch`.")
  instead of an empty grid.
- Changing the tree keeps the current view. Reset is a Units-view control
  and is hidden in the Watched view, which has nothing to reset (the shared
  time range is not in the URL). *(Builder-check call.)* *(Superseded 2026-10-23: see docs/design.md, Page. The
  Watched view now has state (`sort`, `flat`); Reset still restores only the
  Units view's.)*
- In the Watched view, Group by, Bands, Min peak, Rest of tree, Reset, the legend,
  the hidden-chips row and the table are hidden. The Units view's state in the
  hash (group, in, hide, …) is kept, so switching back restores it.
- Header text is per view. Units: today's text. Watched: "Each card is a
  watched directory's whole size (rbytes, including its subdirectories) over
  the shown range. Each card has its own scale: compare shapes, not heights."

### Trend card

Following the 2b mock:

- Name: path relative to the tree root; the absolute path on hover (`title`).
  Untrusted: `textContent` only.
- Size at the end of the shown range.
- Change tag: end minus start of the shown range, absent counting as 0 (as
  for units), formatted with `formatDelta`. Terracotta when > 0, sage when
  < 0, neutral when 0 ("±0 B").
- Sparkline: filled area plus line, y-scale fit to the dir's min and max over
  the shown range (not the full history, so zooming reveals shape). The
  scale's bottom and top values are printed small on the card. A flat series
  draws a flat line mid-height. Null points are gaps.
- Hover shows the time and size at the pointer.
- Click (or Enter on the focused card) opens the Units view drilled into the
  dir (`view` removed, `in=<dir>`). Watched dirs are in `rbytes`, so the
  Units view accepts them as `in`. A dir with no units under it shows an
  empty stack; the summary line must then say "no units here" rather than
  look broken. A watched dir that is ROOT drills to ROOT.
- Grid: responsive, cards about 320 px minimum width.

### Time range

- One time-range slider for the Watched view, kept visible at the bottom of
  the viewport while the card grid scrolls (sticky).
- The range (`range` in main.js) is shared between views: zooming in one is
  the same range in the other.
- Changing it recomputes every card's scale, size and change tag.

### Colors

- New tokens in `web/style.css`, light and dark: growth (terracotta, the
  mock's accent) and shrinkage (sage, the mock's accent-2), each with a line
  and a fill step, plus a neutral for no change. Validate contrast with the
  dataviz palette check. The 8-slot series palette is not used here, so the
  number of cards is not limited to 8.

### Code layout

Per docs/conventions.md: pure shaping in `model.js` (e.g. a function returning
per-card `{path, values, start, end, change, lo, hi}` for a range), a chart
module `web/js/charts/trendCard.js` building a card's ECharts option, and
`main.js` owning state and DOM.

## Left to the builder

- Rendering: one ECharts instance per card (SVG renderer likely lighter at
  ~100 cards) or plain SVG sparklines with a custom hover. The cap (100) is
  the bound to design for. *(Superseded 2026-10-23: see docs/design.md, Page. Plain SVG, sized for thousands of
  cards.)*
- How the Watched view's slider is built (e.g. a small ECharts instance with
  only a dataZoom, its data shadow from ROOT's rbytes) and kept in sync with
  the Units chart's slider without feedback loops.
- ECharts instances in hidden containers are sized 0×0: resize on show.
- Exact copy, spacing and card markup; the mock is the reference.

## Touched

- `src/dirhistory/history.py`, `generate.py` (and maybe `watch.py`):
  `--watch`, `--max-watched`, matching, warnings, `watched` in both files.
- `web/index.html`, `web/js/main.js`, `web/js/model.js`,
  `web/js/charts/trendCard.js` (new), `web/style.css`.
- `deploy/watch.toml.example` (new); `deploy/dirhistory.service`: add
  `--watch /etc/dirhistory/watch.toml`.
- `README.md` (option, Using the page table, Deploy), `docs/design.md`
  (decision, data-file tables), `docs/terms.md`.
- Tests: pytest for config parsing and rejection, component-wise matching
  (`*` not crossing `/`, wildcards in the tree part, non-UTF-8 names),
  a never-changed watched dir getting a series, a deleted watched dir, order
  and dedup, the cap and the warnings, watched dirs' ancestors kept in
  `rbytes`, a missing `--watch` file. `tests/data/web/tree-1.json` and
  `trees.json` gain `watched`; `scripts/check_web.py` checks the view switch,
  card order, click-through to Units (every crumb above the dir works),
  reload keeping `view`, changing tree keeping `view`, Reset hidden in the
  Watched view, and the switch hidden with no watched dirs (a second fixture
  without `watched`).
