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
ancestors. On that database, all 4 trees take 11 s and 94 MB.

### Propagation lag

CephFS propagates rstats upward with a delay, so within one sample a
child can have grown while its parent has not. The parent's own size then
dips by the child's growth for one sample, which would make it a *ghost
unit*: 8.6 GiB dips were seen on the real database. Two measures:

- Time points are the last sample per UTC bucket of at least 1 hour, not
  every 5-minute sample. This hides lag within a bucket.
- Significance uses a 3-point running median of the own series, which drops
  a one-point dip when the lag straddles a bucket boundary.

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

## Data files

`web/data/trees.json` lists the trees:
`[{id, label, detail, file, last_sample, units}]`.

`web/data/tree-<id>.json`:

| Key | Content |
|---|---|
| `generated` | When the file was written (UTC). |
| `tree` | `{id, fs_path, fs_name, cluster, max_depth}`. |
| `threshold` | Bytes of own-size change that made a unit. |
| `times` | Time points, oldest first. |
| `rbytes` | `{path: [bytes or null per point]}` for ROOT (`.`), every unit, and every ancestor of a unit. null: absent. |
| `units` | `{path: {own: [bytes per point, ≥ 0], leaf: bool}}`. |

## Risks and limitations

- **Directory errors.** A later `open` or `readdir` error on a directory
  leaves its children unprobed. They then look constant, and the directory's
  NULL rbytes are carried forward, so changes below it go unseen. The
  generator warns when a tree's samples have errors.
- **Lag at the newest point.** The running median cannot correct the last
  point, so a lag dip there can make a ghost unit until the next run.
- **Magnitude hides change.** A stacked area shows size. A unit of 59 TiB
  that grew by 0.5 TiB looks flat beside small units. The tooltip and table
  show the change; a net-change view (design 2c) will show it directly.
- **Threshold is global.** One threshold for all trees. A tree with many
  medium changes (`/sim`: 76 units) gets many units, and one with few gets
  few. Tune `--threshold` if a tree looks empty or crowded.
- **Hash and odd names.** A hidden path that is not valid UTF-8 cannot
  round-trip through the URL hash, so its hiding is lost on reload.
- **Lost trees.** Data files of trees removed from the database stay in
  `web/data/` (no longer listed in `trees.json`) until deleted by hand.
