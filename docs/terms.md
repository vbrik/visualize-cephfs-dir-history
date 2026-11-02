# Terms

trackdir's own terms (sample, rstats, ROOT, …) are in its
[docs/terms.md](https://github.com/vbrik/track-cephfs-dir-sizes/blob/master/docs/terms.md).

| Term | Meaning |
|---|---|
| tree | A CephFS directory sampled by trackdir to one `max_depth`; a row of `trackdir.tree`. One page view shows one tree. |
| rbytes | CephFS recursive bytes: the size of a directory's whole subtree. Nested directories' rbytes overlap. |
| set | A named list of dir groups of one tree, in its `[tree."/fs/path".set."NAME"]` section of the `--sets` file, plus the implicit **Top level** set (`*`, depth 0). Both views show one set at a time ("Set"). |
| dir group | One entry of a set's `dirs`: `{ match = [patterns], depth = N }`. A pattern is relative to the tree root; wildcards match within one path component; `.` is the root; no `**`. |
| set dir | A dir that a pattern of one of the set's groups matches. |
| depth (of a set dir) | How many levels below the set dir the data file stores, so the page can split or drill into it: 0 = the dir only, 1 = also its subdirs, … The largest `depth` of any group, in any set, matching the dir; limited by the tree's `max_depth`. |
| stored dir | A dir whose series the tree file holds: a set dir, or a dir within its depth below one. |
| split dir | A stored dir whose subdirs are all stored (the file's `split` list). Only these can be split or drilled into. |
| Depth (page) | How many levels below the set dirs (or the drilled-into dir) the Stack view splits; 0 = the dirs themselves. A dir not in `split` stays whole. |
| Top (page) | How many bands the Stack view lists (default 64, max 512): picks first, then the first in the legend's sort, by default the largest spans. The rest are not drawn. |
| band | One stacked area: a dir's rbytes, or an own-files band. |
| own-files band | `X/ bytes`: in a split dir X, the bytes of the files directly in X: rbytes(X) − Σ rbytes(X's stored subdirs), at least 0. |
| drill | Click a band of a split dir to show only what is inside it, split one level (`in=` and Depth 1 in the URL hash). |
| hidden | A band removed from the chart at every Depth: a dir band takes the dir's whole subtree with it, an own-files band only the files directly in the dir. The total shrinks. |
| listed / drawn | The Stack legend lists Top bands; of those, the ticked ones are drawn. |
| pick | A legend band ticked while picking (`pick=` in the URL hash; *None* starts with none). Unlike hidden, an unticked band stays listed and its bytes stay in the bands above it at other Depths. |
| time point | One chart x position: the last sample in a UTC time bucket. Recent history (`--recent`) has the finest buckets (`--min-bucket`, 1 h); older history coarser ones, to stay within `--max-points`. |
| trend card | One set dir's card in the Trends view: its rbytes (whole size) over the shown range, the change, and a sparkline on the card's own scale. |
| span | A band's or card's largest minus smallest size in the shown range, absent counting as 0. The Stack view ranks bands by it (Top), and the Trends view sorts cards by it by default. |
| peak | A band's or card's largest size in the shown range (the table's Peak column; the Trends *Peak* sort). |
| unchanged | A band or trend card whose span is 0: constant, or absent throughout the shown range. Filtered out unless *Show unchanged* is on. |
| filtered out | Left out of a view by its filters (Min GiB: peak under it; Show unchanged off: unchanged). Each view has its own (Stack `smin`, `sflat`; Trends `min`, `flat`). Unlike hidden, nothing is subtracted: a filtered band's bytes stay in the bands above it at other Depths. A ticked band is never filtered out. |
| Stack view / Trends view | The page's two views (`view=stack`, the default, or `view=trends` in the URL hash): stacked bands of a set's dirs, or a trend card per set dir. |
