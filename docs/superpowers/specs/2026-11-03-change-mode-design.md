# Change mode: rates of change in both views — design

Status: draft (2026-11-03).

## Intent

Both views chart values: a band's or card's rbytes or rentries. The scale is
set by the largest dirs, so a big dir's growth is hard to see next to its
size, and in the Stack view a small dir's growth is lost under a big dir's
bulk. For some uses only growth matters: a 1 GiB dir that grew 100 GiB and a
2 TiB dir that grew 100 GiB are the same event.

The Trends view already shows each dir's own shape, but on its own scale and
on separate cards. The reader needs the growth of many dirs on one chart, as
the Stack view stacks sizes, to spot the fast-growing dirs quickly and to
judge their growth rates against each other.

**Change mode** charts each band's and card's **rate of change** at every
time point instead of its value, in both views and for both metrics.

## Goals

- A page-wide Size / Change toggle (`mode=change` in the URL hash; absent =
  Size, today's behavior), next to Bytes / Entries and orthogonal to it.
  Shared by both views, like `metric`.
- A rate, not a raw difference: point *i*'s rate is
  (value[i] − value[i−1]) ÷ (time[i] − time[i−1]). Time points can be of
  different widths (`--recent`, gaps in sampling); a raw difference over a
  1 w point would look 168× a 1 h one. The unit only has to be consistent:
  per day (*my call*), shown as e.g. "+12.3 GiB/d", "+4.5 k entries/d".
- Stack view in Change mode: growth stacks above a zero line, shrinkage
  below, so one point can show "+300 GiB/d here, −50 GiB/d there" without
  them cancelling.
- Trends view in Change mode: a card's sparkline is its rate over time, and
  **all shown cards share one vertical scale**, so a sparkline twice as tall
  means twice the rate.
- A *Smooth* checkbox that removes propagation-lag artifacts from the Stack
  view's subtracted bands, in both modes.

## Non-goals

- Rates in the generator or the data files: rates are derived in the page
  from the values it already has. No data format change.
- Smoothing in the Trends view: cards are whole dirs' rbytes or rentries,
  never a subtraction, so lag cannot make them dip.
- General smoothing (moving averages, softening every short spike).
- Change since the range's start (a cumulative mode): not asked for.

## Anti-goals

- A dir growing at a steady rate must not be filtered out as "unchanged" or
  sink out of Top because its *rate* does not vary (see Filters and ranking).
- Change mode must not change which bands or cards a view lists, beyond
  what the reader sees as different: switching Size ↔ Change keeps the same
  bands, picks, hides, drill and range.

## Decisions

### Rates

- Rates are computed from the values the view would chart in Size mode,
  after hiding's subtraction, the clamp at 0 and smoothing. A band's rates
  therefore add up exactly as its values do: the stack's net rate is the
  rate of the stack's total.
- Absent counts as 0, as everywhere else: a dir created at point *i* has a
  rate spike at *i*.
- The first point of the whole history has no rate (drawn as 0 in the
  Stack, a gap at the start of a sparkline). The first point of a zoomed
  range uses the point before it, so zooming does not change a point's
  rate.

### Stack view in Change mode

- ECharts 5.6's stacking (`stackStrategy: "samesign"`, the default) stacks
  positive values up from 0 and negative ones down from 0. The y axis drops
  `min: 0`.
- **Step areas** (the reader's choice): point *i*'s rate is drawn flat over
  its own interval, (time[i−1], time[i]] (ECharts `step: "start"`, to be
  confirmed against the vendored build), since a rate is the average over
  that interval. Samesign stacking places each point separately, so with
  straight segments a band whose rate changes sign between two points
  would cut diagonally from the positive stack to the negative one across
  every band between; as steps, a sign change is a vertical jump at the
  bucket boundary. Rejected: a positive and a negative series per band (up
  to 1024 series at Top 512, too slow; series ids would no longer be band
  keys) and stacked bars (hairlines at thousands of points). Size mode
  keeps straight segments.
- **Which bands are listed (Top) is unchanged**: ranked by the span of the
  *value* (max − min over the range), as in Size mode. The reader's rule: a
  dir that grew a steady 20 GiB/h all week (3.4 TiB) beats one that hit
  500 GiB/h for an hour and stopped, because it grew more overall. Size
  span gives exactly that, and a dir that shrank 2 TiB ranks with one that
  grew 2 TiB.
- **Stacking order is by the span of the rate** (max rate − min rate over
  the range), the least varying at the bottom: steady growers form the
  base nearest the zero line, bursts sit on the outside. Size mode keeps
  stacking by value span.
- Filters (Min GiB / Min entries on the value's peak, *Show unchanged* on
  the value's span) apply to values, as in Size mode, so both modes list the
  same bands. "Unchanged" means the size never changed, i.e. every rate is
  0, never "the rate never changed".
- Tooltip: each band's rate at the hovered point, in the legend's row
  order as now, plus the point's net rate (sum of all drawn bands'
  rates). Rows for a rate of 0 can be left out (*builder's call*).
- Legend sorts (`lsort`): name, size and delta stay as they are (they are
  about values). *Builder's call* whether to add a rate column.
- Click hit-testing (`model.layerAt`) must follow the signed step stack:
  at time *t*, use the point whose interval holds *t* (no interpolation);
  a layer's span is in the positive stack if its rate there is ≥ 0, else
  in the negative stack.
- No band gaps in Change mode (gap width 0; *my call*). Nearly every band
  has some point with a tiny non-zero rate, so `gapWidths`'s rule would
  drop almost every gap anyway, and with signed stacking a band's neighbor
  changes from point to point; colors separate the bands. Size mode keeps
  its gaps.
- Summary: the net rate at the range's end in place of the total size
  (*builder's call* on wording, e.g. "net +1.2 TiB/d at the end"). Table:
  the drawn bands' average rate over the range ((end − start) ÷ duration)
  and peak rate, beside or in place of Start / End / Change / Peak
  (*builder's call*).

### Trends view in Change mode

- A card's headline number is its **average rate** over the shown range:
  (end − start) ÷ (time[b] − time[a]), e.g. "+20.4 GiB/d".
- Its sparkline is its rate at each point of the range, drawn as steps like
  the Stack view's (each point flat over its own interval).
- **One shared vertical scale** for all cards that pass the filters, from
  min(0, lowest rate) to max(0, highest rate) over the range, with a zero
  line on every card. The fill reaches to the zero line, not to the
  scale's bottom. Size mode keeps each card's own scale.
- Sorts, the growing / shrinking / flat grouping, *Show unchanged*, Min GiB
  / Min entries: unchanged, on values. A 1 GB dir that grew by 100 GB has a
  peak of 101 GB and passes Min GiB.
- The sparkline's hover tooltip shows the rate at the point. Clicking a card
  opens the Stack view as now; Change mode stays on (it is page-wide).
- The slider's shadow (the set's total): its rate in Change mode
  (*builder's call*: the total's value is also acceptable, as the slider is
  only for choosing a range).

### Smoothing (Stack view, both modes)

CephFS propagates rstats upward with a delay, so a child's change can show
before its parent's within a sample. A value made by subtraction then has a
one-point outlier: an own-files band (`X/ bytes`) dips when a child grows
and bumps up when a child shrinks; a dir band with a hidden subdir's bytes
subtracted does the same. In Change mode a dip becomes a loud pair of
spikes, down then up.

- A *Smooth* checkbox in the Stack controls, **on by default**
  (`smooth=0` in the URL turns it off), applies in Size and Change mode.
  This reverses docs/design.md's "No smoothing" decision: the views are for
  long-term trends, production samples are hourly at most, and losing a
  real one-point change (even one 1 w point in a year) is acceptable.
- What is smoothed: only values made by subtraction (own-files bands, and
  dir bands with hidden bytes removed), and only at points where a
  subtracted child's value changed (differs from the point before) — per
  AGENT-LESSONS-LEARNED.md, only those can lag, and leaving every other
  point alone keeps real one-point changes elsewhere (*my call*).
- When: over the whole history, in the pass that builds band values, not
  per zoomed range, so zooming never changes a value, a span or Top. The
  history's first and last points are left as they are (*my call*).
- How: a 3-point median over the subtracted value, before the clamp at 0
  (*my call*). It removes a one-point outlier in either direction and leaves
  a real step change (a value that moves and stays) where it is.
- Before relying on it, measure on the real database (see memory: real DB
  at ceph1-admin): how many points it changes, and the largest outlier left
  after it, for own-files bands at Depth ≥ 1 over the hourly history, in
  both metrics. Record the numbers in docs/design.md's Propagation lag
  section. If a 3-point median leaves lag artifacts that are two points
  wide, come back with the numbers before widening the window.

### URL state and reset

- New keys: `mode` (`change`; absent: size), `smooth` (`0`; absent: on).
  Both survive changing tree, set, Depth or drill, like `metric`.
- Reset restores the Stack controls as now; *builder's call* whether it
  resets Smooth (it is a Stack control) — it does not reset `mode`, which
  is page-wide like `metric`.

## Constraints

- Static page, no new dependencies; ECharts stays vendored at 5.6.
- Stack re-render cost grows with series count (AGENT-LESSONS-LEARNED.md:
  0.6–2 s at Top 512). Rates and smoothing are O(points) per band and must
  not add a second pass over all of a level's bands beyond the existing one.
- Trends must stay plain SVG sparklines at thousands of cards.
- Real paths from the live database must be anonymized before they reach
  git (tests, docs).
- Docs to update: README (the toggle), docs/terms.md (rate, Change mode,
  Smooth), docs/design.md (Change mode, Propagation lag's reversed
  decision, URL keys).

## Left to the builder

- Module layout (e.g. a `rates(values, times)` and `median3` in model.js).
- Tooltip, table and summary wording, and the toggle's exact look.
- Formatting of rates (a `formatRate` reusing `formatBytes` / `formatCount`).
- Tests: model functions under `scripts/check_web.py`'s existing approach.

## Known risks

- **One burst flattens the shared Trends scale.** A card that hit
  500 GiB/d for one point makes a steady 20 GiB/d card's sparkline 4% tall.
  The headline average rate still ranks them; a clipped scale (e.g. at a
  percentile) is a possible follow-up, not part of this change.
- **Spiky charts at fine resolution.** Hourly rates over thousands of
  points are noisy; zooming out does not average them (each point stays one
  bucket's rate). Smoothing removes lag artifacts only, not real noise.
