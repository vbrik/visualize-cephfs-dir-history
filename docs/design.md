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

### Stack own sizes, not rbytes

rbytes covers a whole subtree, so stacking a directory and its ancestors
counts the same bytes once per level: a 618 GB change deep in `/npx/ana`
showed up in every directory above it, up to ROOT. Charts stack *own sizes* (see
[terms](terms.md)) instead. They never overlap and add up to the tree's
total, so the remaining bytes ("Rest of tree") are exact, and hiding a unit
really removes its bytes.

### Units: significant own-size change

Users care about directories whose own files changed: leaves, and
directories with many files of their own. A directory becomes a unit when
its own size ranges by at least `--threshold` (default 10 GiB) over the
history. Absent counts as 0, so new and deleted directories count fully.
On the database of 2026-10-22, this gives 6–76 units per tree.

### Only changed paths are processed

A path with no row after the first sample is constant, and only changed
paths and their parents can change own size. The generator streams the
first sample once (up to ~2 M rows), summing unchanged children per
candidate parent, and fetches full history only for changed paths and their
ancestors. Both are streamed, so memory depends on the number of changed
paths and time points, not on the length of the history.

### Propagation lag

CephFS propagates rstats upward with a delay, so within one sample a
child can have grown while its parent has not. The parent's own size then
dips by the child's growth for one sample, which would make it a *ghost
unit*: 8.6 GiB dips were seen on the real database. Two measures:

- Time points are the last sample per UTC bucket of at least 1 hour by
  default (`--min-bucket`), not every 5-minute sample, so far fewer lagging
  samples are used. A finer `--min-bucket` brings ghost units back.
- For a directory with changed children, significance uses a 3-point
  running median of its own series, which drops a one-point dip. Only such
  directories can dip this way, so the others (leaves, and directories whose
  children are constant) use their raw series, and a real change lasting one
  point still makes a unit.

### Time resolution

A tree has at most `--max-points` time points (400), so files and charts
stay small however long the history grows. History within `--recent` (7
days) of the latest sample keeps the finest buckets (`--min-bucket`, 1 h),
so recent changes stay visible when zoomed in. Older history gets the finest
of 1 h, 6 h, 1 d, 2 d or 1 w that fits the remaining points, or, once even
1 w does not (after about 4.5 years at the defaults), the smallest multiple
of 1 w that does. The generator refuses a `--recent` that alone needs
`--max-points`.

### Watched directories

Units answer "what changed anywhere", at whatever depth the change is. People
also track chosen directories, usually above unit depth and usually not units
(`IceCube/2026/filtered`), and want each one's shape. The Watched view shows
those, one trend card each.

- The list is a TOML file read by the generator (`--watch`), not edited in
  the page: the site is static, and the list is the operator's decision.
- Patterns are absolute paths with wildcards matched per component
  (`watch.py`), so `*` never crosses `/` and next year's directory joins
  without an edit. No `**`: a recursive pattern could watch thousands of
  directories by accident. `--max-watched` (100) caps the cards per tree.
- Cards show rbytes, the whole directory, not own size: a watched directory
  asks "how big is this", and watched directories may nest, since cards are
  never added up.
- Matching rides the generator's existing pass over the first sample, and
  watched directories are fetched explicitly, since one that never changed
  is otherwise never read. Their ancestors are kept too, so the page can
  drill into a watched directory and back up its path.
- Config mistakes are errors. A pattern that matches nothing, or applies to
  no tree, is a warning; with `--tree`, only for the trees read.

### Page

- ECharts (vendored, pinned): a stacked area with a zoom slider today;
  treemap, heatmap and bar charts for the next views.
- "Other" is split into *Smaller units* and *Rest of tree*. Rest is off by
  default, because units are GiB–TiB while the rest is PiB, and drawing it
  flattens every unit to a sliver. Its value stays in the tooltip.
- Bands are ranked by their peak over the zoomed range, so a directory that
  shrank or was deleted stays visible. At most 8 bands are named, the size
  of the validated palette; the rest are Smaller units.
- View state is in the URL hash, so a view can be bookmarked or shared.
  Hash values carry paths as their file-name bytes, percent-encoded, so
  names that are not UTF-8 survive (`model.encodeValue`).
- Hiding takes a unit (`hide=dir`) or a group, i.e. every unit in a subtree
  (`hide=dir/`); hiding a unit's own files leaves the units below it.
- The Watched view (`view=watched`) is a separate view, not a section under
  the chart: the unit controls do not apply to it, so it hides them. It has
  its own slider, pinned to the window's bottom while the cards scroll; the
  zoomed range is shared, so switching views keeps it. A card's change and
  scale follow the range, so zooming reveals a shape. Cards color by
  direction (growth warm, shrinkage cool), so their number is not tied to
  the 8-slot palette. Clicking a card opens the Units view in its dir.

## Data files

`web/data/trees.json` lists the trees:
`[{id, label, detail, file, last_sample, generated, units, watched}]`;
`generated` is the tree file's, so the page refetches a file only when it
changed; `watched` counts the tree's watched directories.

`web/data/tree-<id>.json`:

| Key | Content |
|---|---|
| `generated` | When the file was written (UTC). |
| `tree` | `{id, fs_path, fs_name, cluster, max_depth}`. |
| `threshold` | Bytes of own-size change that made a unit. |
| `times` | Time points, oldest first. |
| `rbytes` | `{path: [bytes or null per point]}` for ROOT (`.`), every unit, every watched directory, and every ancestor of these. null: absent. |
| `units` | `{path: {own: [bytes per point, ≥ 0], leaf: bool}}`. |
| `watched` | Watched directories' paths, in display order (`--watch` order; by path within a pattern). `[]` without `--watch`. |

## Risks and limitations

- **Directory errors.** A later `open` or `readdir` error on a directory
  leaves its children unprobed. They then look constant, and the directory's
  NULL rbytes are carried forward, so changes below it go unseen. The
  generator warns when a tree's samples have errors.
- **Lag at the first and newest points.** The running median cannot correct
  the end points. A lag dip at the newest point can make a ghost unit until
  the next run; one at the first point lasts until the bucket width changes.
  Only directories with changed children are affected, and dips seen so far
  (≤ 8.6 GiB) are below the default threshold.
- **Magnitude hides change.** A stacked area shows size. A unit of 59 TiB
  that grew by 0.5 TiB looks flat beside small units. The tooltip and table
  show the change; a net-change view (design 2c) will show it directly.
- **Threshold is global.** One threshold for all trees. A tree with many
  medium changes (`/sim`: 76 units) gets many units, and one with few gets
  few. Tune `--threshold` if a tree looks empty or crowded.
- **Lost trees.** Data files of trees removed from the database stay in
  `web/data/` (no longer listed in `trees.json`) until deleted by hand.
