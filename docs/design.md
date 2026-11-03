# Design

## Overview

```
trackdir sample ──► PostgreSQL (schema trackdir) ──► dirhistory-generate ──► web/data/*.json
   (every 5 min)                                       (hourly timer)              │
                                                                                   ▼
                                                 browser ◄── nginx serves web/ (static)
```

The site is static: no code runs when a page is requested. A timer runs
`dirhistory-generate`, which reads the database and replaces the data files;
the page fetches them and draws the charts in the browser. Access control is
outside this application.

## Decisions

### Stack rbytes of chosen dirs, split by Depth

rbytes covers a whole subtree, so stacking a directory and its ancestors
counts the same bytes once per level. The Stack view avoids it by stacking
dirs that do not nest: a set's dirs (see [Sets](#sets)), and, where the
reader asks for more detail (Depth, drilling), a dir *replaced* by its
parts. A split dir X becomes `X/ bytes`, the files directly in it (rbytes(X)
minus its subdirs' rbytes, at least 0), plus one band per stored subdir,
recursively, so the bands add up to X's rbytes and no bytes go missing.
Absent counts as 0.

Dirs are chosen, not computed: people look at a tree through particular
dirs at particular depths, and the sets file says which.

### Only stored dirs are read

The generator streams the paths of the first sample once (up to ~2 M rows)
through a matcher for all the tree's sets, adds the paths with later rows (dirs
created later), and fetches the history of the stored dirs only. A stored
dir with no row after the first sample is constant, so it is not stored as a
list (see [Data files](#data-files)). The first sample and the stored dirs'
rows are streamed, so memory depends on the number of changed paths, stored
dirs and time points, not on the length of the history.

### Two metrics: rbytes and rentries

People ask both "where did the space go" and "where did the files go"
(a dir of millions of small files strains the MDS, not the disks). The page
charts one metric at a time, chosen by a page-wide toggle (`metric=`):
rbytes, or rentries = rfiles + rsubdirs, the files and dirs in the
subtree. rsubdirs counts the dir itself, so rentries is at least 1, and an
own-files band `X/ entries` is the files directly in X plus 1. The sum is
one number because the question is "how many inodes", and a split by type
would double the bands.

- Everything else is the same for both: bands, splitting, hiding,
  ranking, picks and filters work on whichever series is shown. Band keys
  are paths, so the toggle keeps the view (drill, hides, picks, colors).
- Min is per metric as well as per view (`smin`/`sminent`,
  `min`/`minent`), since no threshold suits both a GiB and an entry count;
  Min entries defaults to 1000. Show unchanged is per view only.
- trackdir writes a dir's row whenever any of its columns changes, rfiles
  and rsubdirs included, so carrying values forward between rows is exact
  for both. A NULL rfiles or rsubdirs (an rstats error) keeps the previous
  rentries, as a NULL rbytes keeps rbytes.
- The tree file pairs both series per path (see [Data files](#data-files)):
  paths are most of a large file, and a second map keyed by path would
  have doubled it.

### Propagation lag

CephFS propagates rstats upward with a delay, so within one sample a
child can have grown while its parent has not. A split dir's own-files band
(`X/ bytes`, `X/ entries`) then dips by the child's growth for one point,
and goes negative if the child is large; the page clamps it at 0. Only
own-files bands are affected, and a dip can lift one into the Top by a
single-point spike.

- Time points are the last sample per UTC bucket of at least 1 hour (fixed:
  `MIN_BUCKET`), not every 5-minute sample, so far fewer lagging samples are
  used.
- *Smooth* (a Stack checkbox, on by default; `smooth=0` turns it off), in
  both modes: at each point where a subtracted dir changed (the only places
  lag can show), a subtraction (an own-files band, or a dir band less
  hidden bytes) takes the median of its value at that point and its two
  neighbors (`model.smoothLag`), before the clamp at 0 and over the whole
  history, so zooming never changes a value. It removes a one-point dip or
  bump and keeps a step. This replaces an earlier "no smoothing" decision
  (it would also drop a real one-point change): the views are for long-term
  trends, production samples are hourly at most, and losing a real change
  that lasts one point, even one week-wide point, is acceptable; in Change
  mode a dip becomes a loud pair of opposite spikes.

Measured on the real data files on 2026-11-03 (4 trees, 272 hourly points
over 12 days, 20 215 split dirs, own-files values before the clamp):
Smooth changes only the points that are one-point outliers (rbytes 214,
299, 89, 327 per tree; rentries 78, 131, 35, 134) and leaves about a tenth
of them (rbytes 27, 32, 6, 34; the largest left 0.14 GiB, against up to
30.1 GiB removed); points below 0 fall by half or more (deepest −0.40 →
−0.15 GiB, −216 → −14 entries). Two-point dips (two points below both
outer neighbors) it leaves as they are: rbytes 27, 34, 6, 27 per tree, at
most 0.15 GiB but for one of 5.5 GiB. A wider window would take those too,
and more real changes with them; it is not used.

Measured earlier on a real database, over 2 days of hourly points, the largest dip
in any own-files band is 0.25 GiB; 8.6 GiB was seen earlier at finer
sampling. Over 11 days (272 hourly points, 4 trees, 20 215 split dirs), the
largest was 0.40 GiB below 0, and 217 entries below 1 in an `X/ entries`
band; dips hit fewer points for rentries (244) than for rbytes (679).

### Change mode: rates of change

The largest dirs set a chart's scale, so a big dir's growth is hard to see
next to its size, and in the Stack view a small dir's growth is lost under
a big dir's bulk; for some questions only growth matters. A page-wide
Size / Change toggle (`mode=change`), orthogonal to Bytes / Entries,
charts each band's and card's rate of change instead of its value.

- A rate, not a difference: point i's is (v[i] − v[i−1]) over the time
  since point i−1, per day (`model.rates`), so points of different widths
  (`--recent`, gaps in sampling) compare, and a gap's growth spreads over
  it. It covers the interval (t[i−1], t[i]]; a range's rates are those of
  points a+1..b. Absent counts as 0, so creation and deletion are spikes.
- Stack: the same bands as Size mode (Top still ranks by value span: a dir
  that grew a steady 3.4 TiB over a week beats one that hit 500 GiB/h for
  an hour, because it grew more), stacked instead by rate span, steady
  growers at the bottom, bursts outside. Filters and *Show unchanged* stay
  on values: a dir growing at a constant rate has a rate span of 0 but has
  changed.
- Step areas (ECharts `step: "start"`): ECharts' samesign stacking places
  each point separately, growth up from 0 and shrinkage down from it, so
  straight segments of a band whose rate changes sign would cut diagonally
  across every band between; steps make that a vertical jump at the bucket
  edge, with one series per band. Rejected: a positive and a negative
  series per band (up to 1024 series at Top 512, too slow) and bars
  (hairlines at thousands of points). No gaps between bands: nearly every
  band has some tiny rate, and its neighbor changes with the sign.
- ECharts hands an axis tooltip only the nearest point, even unsnapped,
  half a step off; the page records the pointer's time (a capture listener
  on the chart) and the tooltip and clicks use the step under it
  (`model.stepIndex`).
- Trends: a card's number is its average rate over the range, its
  sparkline its rates as steps, and all shown cards share one scale
  (`model.rateScale`), so heights compare. A single burst flattens the
  rest; the average rates still rank them, and a clipped scale is a
  possible follow-up.
- Rates are derived in the page from the values it has: no data format
  change.

### Time resolution

A tree has at most `--max-points` time points (5000), so files and charts
stay bounded however long the history grows; years of history must fit, at
a coarse resolution. History within `--recent` of the latest sample keeps
1 h buckets; by default `--recent` is 0, so the whole history shares one
width. History older than that gets the finest of 1 h, 6 h, 1 d, 2 d or 1 w
that fits the remaining points: at the defaults 1 h up to about 7 months,
6 h up to about 3.4 years, 1 d up to about 13.7 years. Once even 1 w does
not fit, it gets the smallest multiple of 1 w that does. The generator
refuses a `--recent` that alone needs `--max-points`.

### Sets

People track chosen dirs, usually above the depth where bytes change
(`IceCube/2026/filtered`), and want both the stack of a few of them and each
one's shape. A **set** is a named list of such dirs; the Stack and Trends
views each show one set at a time.

- The sets are a TOML file read by the generator (`--sets`, required), not
  edited in the page: the site is static, and the list is the operator's
  decision.
- The list is per tree: `[tree."/fs/path".set."NAME"]` sections, named by
  the tree's root and a set's name, hold `dirs`, a list of groups
  `{ match = [patterns], depth = N }`. Patterns are relative to the root.
  Trees may overlap (`/npx/user` and `/npx/user/vbrik`), and a global list
  would apply `/npx/user/*` to the inner tree too. `.` matches the root.
  Tree roots are assumed unique, so a section naming two trees (same root on
  different clusters or filesystems) is an error, as is one naming no tree,
  even with `--tree`; a root that is not UTF-8 cannot be named in TOML.
- Every tree has the implicit set **Top level** (`*`, depth 0), always
  first, so a tree is browsable with an empty file. A set of that name in the
  file replaces it, still first, e.g. to give the top-level dirs depth 2.
  The file's other sets follow in its order.
  `watch`, the format before sets, is an error with a hint.
- Sets are shown one at a time because dirs at different depths do not
  compare: `*` and `*/*` side by side mix a dir with its own subdirs. A
  set's name, not a pattern's depth, decides what is compared.
- `depth` is how many levels below each matched dir the data file stores, so
  the page can split and drill into it; 0 is the dir only. A dir matched by
  several groups, in one set or several, is stored to the largest `depth`,
  and the page splits and drills by what is stored, not by the current set's
  own depths: if set B stores `X` 3 levels deep, set A, which gives `X`
  depth 1, can go 3 deep too. Wildcards are matched per component, so `*`
  never crosses `/` and next year's dir joins without an edit. No `**`: use
  `depth` to go deeper, since a recursive pattern could store thousands of
  dirs by accident.
- More than `--max-dirs` (100000) stored dirs in a tree is an error naming
  the sets and their counts. The cap is per tree, since all its sets share one
  file, and checked before any history is fetched. It catches accidents
  only; see the risk "Large depth makes large files".
- Matching rides the generator's single pass over the first sample (one
  matcher for all of a tree's sets), plus the paths with later rows. Stored
  dirs' histories are fetched explicitly, since a dir that never changed is
  otherwise never read.
- Config mistakes are errors. A pattern that matches nothing is a warning,
  for the trees read; the implicit set never warns.

### Page

- ECharts (vendored, pinned): a stacked area with a zoom slider today;
  treemap, heatmap and bar charts for the next views.
- **Stack** bands are rbytes (or rentries, see
  [Two metrics](#two-metrics-rbytes-and-rentries)). Each set dir is one band, or, at Depth N, is
  split N levels (only dirs in the file's `split` list: those with all their
  subdirs stored; any other stays whole). Depth counts levels below the shown
  dirs, which are the set's, or the drilled-into dir's. A band reached from
  two roots, as when a set holds a dir and its own subdir, is drawn once.
- Filters first: bands whose peak in the zoomed range is under Min GiB
  (`smin`, default 1), or that are unchanged in it unless *Show unchanged*
  (`sflat=1`), are filtered out, picks excepted; the summary counts them
  and says why. Hundreds of a level's bands are tiny or constant, and
  would otherwise take Top's slots under a size or name sort. Unlike hide,
  filtering subtracts no bytes from the bands above: it only leaves bands
  out.
- The rest are ranked by span (max − min) over the zoomed range, so a dir that
  shrank or was deleted stays visible. Top (default 64, the palette's size;
  max 512) of them are listed, chosen in the legend's sort (`lsort`): by
  default span, else the largest (`size`) or smallest (`-size`) at the
  range's end, the most grown (`delta`) or shrunk (`-delta`), or the first
  by name. Picks are listed first. The chosen bands stack by span whatever
  the sort, the least changed at the bottom: a steady base under the
  movers, which then sit on top, nearest the total's line. The rest are not drawn and not summed anywhere, and
  there is no "Other" band: the summary says how many are left out, adding
  "(raise Top)" when Top is below 512. Hidden bands are not ranked. Past 64
  bands, colors repeat (see [conventions](conventions.md)), and the chart's
  tooltip lists the 30 largest at the pointer and sums the rest in one row.
- Drilling: clicking a band whose dir is in `split` (legend or chart) shows
  that dir split one level (`in=dir`, Depth 1). `in` is used only if the
  set's dirs reach it through `split`; otherwise the page shows the set.
  Changing the tree or the set clears it.
- Hiding takes a dir band (`hide=dir`) or an own-files band (`hide=dir/.`,
  shown as `dir/ bytes`) and removes those bytes at every Depth. A dir
  removes its whole subtree: the bands under it go, and a drawn band that
  holds it (a dir above it) has its bytes subtracted, at least 0. An own-files
  hide removes only the files directly in that dir. Otherwise a hide made at
  one Depth would leak back in at another while its chip still says it is
  hidden. Changing the tree clears hides.
- Picking: the legend's tick boxes choose which listed bands are drawn
  (`pick=key`, repeated; `pick=` alone: none ticked; no `pick`: all). It is
  for comparing a few of the movers, which are always among the listed
  ones, so it works on the listed bands, not on all a level's thousands:
  unticking only stops drawing, nothing moves into its place, unlike hide.
  Picks are listed even if a zoom or a sort ranks them past Top, and listed bands
  keep their colors when unticked. The chart, its click hit-testing, the
  summary's total and the table cover the ticked bands. Picks are one
  level's band keys, so changing the tree, set or Depth, or drilling,
  clears them; hiding a band drops its pick.
- The legend sorts by name, size at the range's end, or change over the
  range (`lsort=name|size|delta`, `-` before it reversed); its default is
  chart order, the top of the stack (the biggest movers) first, so rows
  line up with the bands.
  When more bands than Top are ranked, sorting also chooses which are listed
  (see Top above); the chart's stacking stays by span. Ticked bands
  come first, each group in that order, so the few picked stay in view in a
  long list.
- View state is in the URL hash (`view`, `metric`, `mode`, `set`, `depth`,
  `top`, `in`, `hide`, `pick`, `lsort`, `smin`, `sminent`, `sflat`,
  `smooth`, `sort`, `min`, `minent`, `flat`), so a view can be
  bookmarked or shared. Hash values carry
  paths as their file-name bytes, percent-encoded, so names that are not
  UTF-8 survive (`model.encodeValue`). Keys and views of older pages are
  ignored.
- A set that matches no dir shows one note in both views: "No directories
  match this set's patterns."
- The Trends view (`view=trends`) is a separate view, not a section under the
  chart: the Stack controls do not apply to it, so it hides them. It has its
  own slider, pinned to the window's bottom while the cards scroll; its
  shadow is the set's total. The zoomed range is shared, so switching views
  keeps it. The Set control and `set=` are shared too; Sort, Min GiB, *Show
  unchanged* and the range are shared by all sets. Min GiB and *Show
  unchanged* are per view (Stack `smin`, `sflat`; Trends `min`, `flat`, its
  older key): a threshold that suits a level's bands need not suit set dirs.
  Min entries likewise (`sminent`, `minent`).
- Trends cards are the current set's dirs, not their stored descendants. A
  card shows rbytes (or rentries), the whole dir, not its own files: it asks "how big is this",
  and set dirs may nest, since cards are never added up. A card's change and
  scale follow the range, so zooming reveals a shape. Cards color by
  direction (growth warm, shrinkage cool), so their number is not tied to
  the 64-slot palette. Clicking a card opens the Stack view in its dir, split
  one level if it is in `split`, else at Depth 0.
- A wildcard can match thousands of dirs (`/npx/user/*`), most of which
  never change. Cards unchanged in the shown range (constant, or absent
  throughout) are hidden unless *Show unchanged* (`flat=1`) is on, as are
  cards whose peak in the range is under Min GiB (`min`, default 1; Min
  entries, `minent`, default 1000; so a dir
  absent throughout needs `min=0` too), and the rest sort by span (max − min) by default, so the movers come first; also
  by peak, current size or name (`sort=`). Every sort groups the cards by
  their change's sign first: growing, then shrinking, then ending where they
  started, since span alone mixes a dir that grew 5 TiB with one that shrank
  5 TiB. Sort keys and the unchanged test
  count absent as 0, like a card's change, so a dir created or deleted in
  the range has changed. Called span, not change, because a card's change is
  end − start.
- Sparklines are plain SVG paths, not ECharts: one ECharts instance per
  card took 20 s for 3000 cards of 28 points and 8.5 s per range change;
  SVG takes 0.6 s and 0.2 s with 400 points. The slider
  still applies a drag on release, since cards reorder and come and go
  with the range.
- Reset restores the Stack view's controls (Depth, Top, drilling, hides,
  picks, the legend's order, Smooth) only, not the page-wide Bytes /
  Entries and Size / Change; it is hidden in the Trends view.

## Data files

`web/data/trees.json` lists the trees:
`[{id, label, detail, file, last_sample, generated, sets}]`; `generated` is
the tree file's, so the page refetches a file only when it changed; `sets`
lists the tree's sets as `{name, dirs}`, `dirs` counting the set's dirs, in
the file's order, "Top level" first.

`web/data/tree-<id>.json`:

| Key | Content |
|---|---|
| `generated` | When the file was written (UTC). |
| `tree` | `{id, fs_path, fs_name, cluster, max_depth}`. |
| `times` | Time points, oldest first. |
| `metrics` | The metrics of each `series` pair, in order: `["rbytes", "rentries"]`. |
| `series` | `{path: [rbytes, rentries]}` for every stored dir: a set dir, or one within its `depth` below a set dir. Each series is a list (a value or null per point, null: absent) if it changed, else one number or null, its constant value. The page reads a file without `series` (written before rentries) from its `rbytes`, `{path: series}`, and disables Entries. |
| `sets` | The tree's sets in `--sets` order, "Top level" first: `[{name, paths}]`, a set's dirs in pattern order, by path within a pattern. The page sorts the Trends cards itself. |
| `split` | The stored dirs whose subdirs are all stored (each less than its `depth` below a set dir, and above `max_depth`), sorted. Only these can be split or drilled into. |

Most stored dirs never change, so one number instead of a list keeps files
small: on a real database 2 866 of the 2 995 series of the largest Top level
set are one number, and its file is 0.3 MB; a 10 678-dir set measured 99.5 %
unchanged. Then paths are most of a file: of a 205 MB rbytes-only file of
1.9 M stored dirs, 143 MB were path keys. Pairing rentries with rbytes per
path made the 4 real tree files (2 474 to 72 069 stored dirs, 272 points)
13 % to 41 % larger than rbytes alone; the largest is 6.1 MB.

## Risks and limitations

- **Directory errors.** A later `open` or `readdir` error on a directory
  leaves its children unprobed. They then look constant, and the directory's
  NULL rbytes are carried forward, so changes below it go unseen. The
  generator warns when a tree's samples have errors.
- **Nested set dirs count twice.** A set that holds a dir and its own
  subdir as separate dirs can draw their bytes twice: the outer dir whole
  and the inner one as its own band. A band reached from two roots is drawn
  once, which does not prevent this. Nothing guards against it; the set's
  author chooses.
- **Lag dips in own-files bands.** A split dir's `X/ bytes` or
  `X/ entries` can dip for a point when a child's value is ahead of its
  parent's (see
  [Propagation lag](#propagation-lag)), and can lift the band into Top. Seen:
  ≤ 0.40 GiB and ≤ 217 entries at 1 h points, 8.6 GiB at finer sampling.
  The page clamps at 0, and *Smooth* (on by default) removes most one-point
  dips; two-point ones stay (see Propagation lag).
- **Bands past Top, or filtered out, are not drawn.** The chart's total and
  the table cover the drawn bands only; the summary says how many are left
  out. By default (Min 1 GiB, unchanged filtered) a big constant dir is
  missing from the stack and its total.
- **Large depth makes large files.** A broad pattern with a large `depth`
  stores every dir below it, and every one that changes costs a number per
  time point; the page downloads the whole file. `--max-dirs` catches
  accidents only, not a file that is large but under the cap.
- **Magnitude hides change.** A stacked area shows size. A band of 59 TiB
  that grew by 0.5 TiB looks flat beside small bands. The tooltip and table
  show the change, and a Trends card has its own scale; a net-change view
  (design 2c) will show it in the stack.
- **Lost trees.** Data files of trees removed from the database stay in
  `web/data/` (no longer listed in `trees.json`) until deleted by hand.
