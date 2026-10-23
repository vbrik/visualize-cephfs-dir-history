# Terms

trackdir's own terms (sample, rstats, ROOT, …) are in its
[docs/terms.md](https://github.com/vbrik/track-cephfs-dir-sizes/blob/master/docs/terms.md).

| Term | Meaning |
|---|---|
| tree | A CephFS directory sampled by trackdir to one `max_depth`; a row of `trackdir.tree`. One page view shows one tree. |
| rbytes | CephFS recursive bytes: the size of a directory's whole subtree. Nested directories' rbytes overlap. |
| own size | A directory's rbytes minus its direct subdirectories' rbytes: the bytes of the files directly in it. For a directory without sampled subdirectories (none, or at `max_depth`) it is its whole rbytes. Own sizes never overlap and add up to ROOT's rbytes. |
| unit | A directory whose own size changed by at least the threshold over the tree's history. Units are what the charts stack. |
| leaf unit | A unit without sampled subdirectories (none ever, or at `max_depth`), so its own size is its whole rbytes; labeled by its path. Other units are labeled `path (own files)`. |
| threshold | The own-size change that makes a directory a unit (`dirhistory-generate --threshold`, default 10 GiB). |
| time point | One chart x position: the last sample in a UTC time bucket. Recent history (`--recent`) has the finest buckets (`--min-bucket`, 1 h); older history coarser ones, to stay within `--max-points`. |
| band | One stacked area: a unit, or a group of units. |
| group | Units summed into their ancestor N levels below the shown directory ("Group by"). Clicking a group shows its units. |
| Smaller units | The units not drawn as their own band, because of "Bands" or "Min peak". |
| Rest of tree | The shown directory's bytes that are in no unit: rbytes minus the own sizes of all its units. Off by default. |
| hidden | A unit, or all units of a group, removed from the chart entirely: their bytes are in no band, so the total shrinks. Hiding a unit's own files does not hide the units below it. |
| peak | A band's largest value in the shown time range; bands are ranked by it. |
| watch pattern | A path pattern, relative to a tree's root, in that tree's section of the `--watch` file, e.g. `IceCube/*/filtered`; wildcards match within one path component. A pattern applies only to its own tree. |
| watched dir | A directory of a tree that one of the tree's watch patterns matches. |
| trend card | One watched dir's card in the Watched view: its rbytes (whole size) over the shown range, the change, and a sparkline on the card's own scale. |
| span | A trend card's largest minus smallest size in the shown range, absent counting as 0; the Watched view's default sort. |
| unchanged | A trend card whose span is 0: constant, or absent throughout the shown range. Hidden unless *Show unchanged* is on. |
| Units view / Watched view | The page's two views (`view=` in the URL hash): stacked units, or trend cards of watched dirs. |
| ghost unit | A directory that looks like a unit only because CephFS propagates rbytes upward with a delay, so its own size dips while a child has grown but it has not yet. See [design.md](design.md#propagation-lag). |
