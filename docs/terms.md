# Terms

trackdir's own terms (sample, rstats, ROOT, …) are in its
[docs/terms.md](https://github.com/vbrik/track-cephfs-dir-sizes/blob/master/docs/terms.md).

| Term | Meaning |
|---|---|
| tree | A CephFS directory sampled by trackdir to one `max_depth`; a row of `trackdir.tree`. One page view shows one tree. |
| rbytes | CephFS recursive bytes: the size of a directory's whole subtree. Nested directories' rbytes overlap. |
| rentries | rfiles + rsubdirs: the files and directories in a directory's whole subtree, the directory itself included (so at least 1). Overlaps like rbytes. |
| metric | What both views chart, chosen by the page's Bytes / Entries toggle (`metric=` in the URL hash): rbytes (the default) or rentries. A band's or card's values, span, peak and the Min filter are in the metric's unit. |
| set | A named list of dir groups of one tree, in its `[tree."/fs/path".set."NAME"]` section of the `--sets` file, plus the implicit **Top level** set (`*`, depth 0). Both views show one set at a time ("Set"). |
| dir group | One entry of a set's `dirs`: `{ match = [patterns], depth = N }`. A pattern is relative to the tree root; wildcards match within one path component; `.` is the root; no `**`. |
| set dir | A dir that a pattern of one of the set's groups matches. |
| depth (of a set dir) | How many levels below the set dir the data file stores, so the page can split or drill into it: 0 = the dir only, 1 = also its subdirs, … The largest `depth` of any group, in any set, matching the dir; limited by the tree's `max_depth`. |
| stored dir | A dir whose series the tree file holds: a set dir, or a dir within its depth below one. |
| split dir | A stored dir whose subdirs are all stored (the file's `split` list). Only these can be split or drilled into. |
| Depth (page) | How many levels below the set dirs (or the drilled-into dir) the Stack view splits; 0 = the dirs themselves. A dir not in `split` stays whole. |
| Top (page) | How many bands the Stack view lists (default 64, max 512): picks first, then the first in the legend's sort, by default the largest spans. The rest are not drawn. |
| band | One stacked area: a dir's rbytes (or rentries), or an own-files band. |
| own-files band | `X/ bytes`: in a split dir X, the bytes of the files directly in X: rbytes(X) − Σ rbytes(X's stored subdirs), at least 0. With rentries, `X/ entries`: the files directly in X plus X itself, by the same subtraction. |
| drill | Click a band of a split dir to show only what is inside it, split one level (`in=` and Depth 1 in the URL hash). |
| hidden | A band removed from the chart at every Depth: a dir band takes the dir's whole subtree with it, an own-files band only the files directly in the dir. The total shrinks. |
| listed / drawn | The Stack legend lists Top bands; of those, the ticked ones are drawn. |
| pick | A legend band ticked while picking (`pick=` in the URL hash; *None* starts with none). Unlike hidden, an unticked band stays listed and its bytes stay in the bands above it at other Depths. |
| time point | One chart x position: the last sample in a UTC time bucket. Buckets are the finest of 1 h, 6 h, 1 d, 2 d or 1 w that keeps a tree within `--max-points`; history within `--recent` (default 0) stays at 1 h. |
| trend card | One set dir's card in the Trends view: its rbytes (whole size) or rentries over the shown range, the change, and a sparkline on the card's own scale. |
| span | A band's or card's largest minus smallest value (size or entries) in the shown range, absent counting as 0. The Stack view ranks bands by it (Top), and the Trends view sorts cards by it by default. |
| peak | A band's or card's largest value in the shown range (the table's Peak column; the Trends *Peak* sort). |
| unchanged | A band or trend card whose span is 0: constant, or absent throughout the shown range. Filtered out unless *Show unchanged* is on. |
| filtered out | Left out of a view by its filters (Min GiB or Min entries, per metric: peak under it; Show unchanged off: unchanged). Each view has its own (Stack `smin`, `sminent`, `sflat`; Trends `min`, `minent`, `flat`). Unlike hidden, nothing is subtracted: a filtered band's bytes stay in the bands above it at other Depths. A ticked band is never filtered out. |
| Change mode | The page's Size / Change toggle set to Change (`mode=change` in the URL hash), in both views and for both metrics: bands and cards chart their rate instead of their value. Which bands and cards are listed, and their filters, do not change. |
| rate | A band's or card's change per day at a time point: (value − the previous point's value) over the time between them, absent counting as 0. It covers the interval since the previous point, drawn as a step; the first point has none. A card's average rate is its change over the shown range's duration. |
| Smooth | The Stack checkbox (on by default; `smooth=0` off) that removes one-point propagation-lag dips and bumps from values made by subtraction (own-files bands, bands less hidden bytes): a 3-point median at points where a subtracted dir changed. |
| Stack view / Trends view | The page's two views (`view=stack`, the default, or `view=trends` in the URL hash): stacked bands of a set's dirs, or a trend card per set dir. |
