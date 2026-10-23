"""Turn a trackdir tree's samples into per-directory time series.

Pure logic, no database: the caller supplies samples and dir_state rows.

rbytes covers a directory's whole subtree, so the series of nested
directories overlap. Charts stack *own sizes* instead: a directory's rbytes
minus its direct children's rbytes, i.e. the bytes of its own files (all of
it for a leaf). Own sizes never overlap and sum to ROOT's rbytes. A *unit* is
a directory whose own size changed by at least a threshold; see summarize.
"""

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from statistics import median

from dirhistory.watch import MAX_WATCHED, Matcher, Parts, Watched, select

ROOT = b"."

# Defaults of Resolution.
# At most this many time points per tree, so a chart stays readable.
MAX_POINTS = 400
# History this recent keeps the finest buckets, so recent changes stay
# visible however long the history grows.
RECENT = timedelta(days=7)
# The finest bucket: an hour, not trackdir's typical 5-minute sampling
# interval. A parent's rbytes can lag its children's within a sample, and one
# sample per hour exposes far fewer lagging samples (summarize smooths out
# the rest).
MIN_BUCKET = timedelta(hours=1)

# Coarser bucket widths for older history, finest first.
BUCKET_WIDTHS = tuple(timedelta(hours=h) for h in (1, 6, 24, 48, 7 * 24))

# A dir_state row: (sample id, path, rbytes, deleted)
type Row = tuple[int, bytes, int | None, bool]
# A series value per chosen time point; None: absent or unknown.
type Series = list[int | None]


@dataclass(frozen=True, slots=True)
class Sample:
    """A trackdir sample of the tree."""

    id: int
    started: datetime


@dataclass(frozen=True, slots=True)
class Unit:
    """A directory whose own size changed significantly.

    own: bytes of its own files per time point, clamped at 0; 0 while absent.
    leaf: whether it has no sampled subdirectories (none ever, or it is at
    the tree's max depth), so own is its whole rbytes.
    """

    own: list[int]
    leaf: bool


@dataclass(frozen=True, slots=True)
class TreeHistory:
    """A tree's chosen time points, its units, its watched dirs, and the
    rbytes series of ROOT, the units, the watched dirs, and their ancestors."""

    times: list[datetime]
    rbytes: dict[bytes, Series]
    units: dict[bytes, Unit]
    watched: Watched = field(default_factory=lambda: Watched([], [], 0))


def parent(path: bytes) -> bytes:
    """Return the parent of PATH, which must not be ROOT."""
    head, sep, _ = path.rpartition(b"/")
    return head if sep else ROOT


def ancestors(path: bytes) -> Iterator[bytes]:
    """Yield the ancestors of PATH, nearest first, ending with ROOT."""
    while path != ROOT:
        path = parent(path)
        yield path


@dataclass(frozen=True, slots=True)
class Resolution:
    """How finely time points sample a tree's history.

    History within RECENT of the latest sample gets MIN_BUCKET buckets.
    Older history gets the finest width among MIN_BUCKET and the coarser
    BUCKET_WIDTHS that keeps all points within MAX_POINTS, else the
    smallest whole multiple of the coarsest of those that does.
    """

    recent: timedelta = RECENT
    max_points: int = MAX_POINTS
    min_bucket: timedelta = MIN_BUCKET

    def recent_points(self) -> int:
        """Return the most points the recent window can take."""
        return self.recent // self.min_bucket + 1


DEFAULT_RESOLUTION = Resolution()


def choose_points(
    samples: Sequence[Sample], resolution: Resolution = DEFAULT_RESOLUTION
) -> list[Sample]:
    """Return the last sample of each time bucket, oldest first.

    SAMPLES are ordered by (started, id). Buckets are UTC-aligned, as
    RESOLUTION says. The latest sample is always the last point. There are
    at most RESOLUTION.max_points points if the recent window fits in them
    (Resolution.recent_points() < max_points, as the CLI enforces).
    """
    if not samples:
        return []
    cutoff = samples[-1].started - resolution.recent
    older = [s for s in samples if s.started <= cutoff]
    recent = _last_per_bucket(
        [s for s in samples if s.started > cutoff], resolution.min_bucket
    )
    budget = max(resolution.max_points - len(recent), 1)
    widths = [resolution.min_bucket]
    widths += [w for w in BUCKET_WIDTHS if w > resolution.min_bucket]
    for width in widths:
        kept = _last_per_bucket(older, width)
        if len(kept) <= budget:
            return kept + recent
    # A span S meets at most S // width + 1 aligned buckets; whole multiples
    # of the coarsest width keep bucket edges stable as history grows.
    span = older[-1].started - older[0].started
    width = (span // (budget * widths[-1]) + 1) * widths[-1]
    return _last_per_bucket(older, width) + recent


def _last_per_bucket(samples: Sequence[Sample], width: timedelta) -> list[Sample]:
    """Return the last of SAMPLES in each UTC-aligned bucket of WIDTH."""
    seconds = width.total_seconds()
    last: dict[int, Sample] = {}
    for sample in samples:
        last[int(sample.started.timestamp() // seconds)] = sample
    return list(last.values())


def summarize(
    samples: Sequence[Sample],
    changed: set[bytes],
    first: Iterable[tuple[bytes, int | None]],
    fetch: Callable[[set[bytes]], Iterable[Row]],
    threshold: int,
    limit: int,
    resolution: Resolution = DEFAULT_RESOLUTION,
    watch: Sequence[Parts | None] = (),
    watch_limit: int = MAX_WATCHED,
) -> TreeHistory:
    """Return the history of a tree with SAMPLES, ordered by (started, id).

    CHANGED holds the paths with a row in any sample after the first. FIRST
    yields (path, rbytes) for the first sample's rows. FETCH(paths) yields
    every row of PATHS, ordered as SAMPLES are. Both are read once, so they
    may stream. RESOLUTION picks the time points.

    A path outside CHANGED has its single row in the first sample, so its
    value is constant, and only CHANGED paths and their parents can change
    own size. A unit is such a directory whose own size ranges by at least
    THRESHOLD bytes over the chosen points. At most LIMIT units are kept,
    widest range first, ties by path.

    A directory with changed children subtracts their rbytes, which CephFS
    may not yet have propagated to the directory's own rbytes, so its own
    size can dip for one point. Its range is taken over a 3-point running
    median, which drops such dips. Other directories cannot dip that way, so
    their raw range counts, including changes that last one point.

    The watched dirs are the paths that any of the first sample's paths or
    CHANGED match in WATCH (watch.Matcher patterns, in config order), at
    most WATCH_LIMIT (watch.select). Their series and their ancestors' are
    kept, so the page can drill into a watched dir and up its path, even
    when they never changed and are not units.
    """
    points = choose_points(samples, resolution)
    dirs = changed | {parent(p) for p in changed if p != ROOT}
    matcher = Matcher(watch)
    constant, has_children = _children(first, changed, dirs, matcher.add)
    for path in changed:
        matcher.add(path)
    watched = select(matcher.matches(), watch_limit)
    shown = set(watched.paths) | {a for w in watched.paths for a in ancestors(w)}
    wanted = {ROOT} | changed | {a for p in changed for a in ancestors(p)} | shown
    rbytes = _replay(samples, points, fetch(wanted))

    children = defaultdict(list)
    for path in changed - {ROOT}:
        children[parent(path)].append(path)
    own = {d: _own(d, rbytes, constant[d], children[d], len(points)) for d in dirs}

    ranges = {
        d: _range(_running_median(s) if children[d] else s) for d, s in own.items()
    }
    picked = sorted(
        (d for d, r in ranges.items() if r >= threshold), key=lambda d: (-ranges[d], d)
    )[:limit]
    units = {
        d: Unit([max(v, 0) for v in own[d]], d not in has_children)
        for d in sorted(picked)
    }
    kept = {ROOT} | set(units) | {a for u in units for a in ancestors(u)} | shown
    return TreeHistory(
        [p.started for p in points],
        {p: rbytes.get(p, [None] * len(points)) for p in sorted(kept)},
        units,
        watched,
    )


def _children(
    first: Iterable[tuple[bytes, int | None]],
    changed: set[bytes],
    dirs: set[bytes],
    visit: Callable[[bytes], None],
) -> tuple[Counter[bytes], set[bytes]]:
    """Return, for DIRS, the summed rbytes of children outside CHANGED, and
    the DIRS that have any child. Calls VISIT with every path of FIRST, so
    callers need not read it again.

    FIRST yields the first sample's (path, rbytes); NULL rbytes count as 0.
    """
    constant: Counter[bytes] = Counter()
    has_children = {parent(p) for p in changed if p != ROOT}
    for path, rbytes in first:
        visit(path)
        if path == ROOT:
            continue
        up = parent(path)
        if up in dirs:
            has_children.add(up)
            if path not in changed:
                constant[up] += rbytes or 0
    return constant, has_children


def _replay(
    samples: Sequence[Sample], points: Sequence[Sample], rows: Iterable[Row]
) -> dict[bytes, Series]:
    """Return each row path's value at each of POINTS, carried forward.

    ROWS must be ordered as SAMPLES are; they are read once, in one pass. A
    value holds until the path's next row. A NULL rbytes (an error) keeps the
    previous value; a tombstone makes it None, as does no row yet. Raises
    ValueError if a row is out of order or of no sample in SAMPLES.
    """
    point_at = {p.id: i for i, p in enumerate(points)}
    rows = iter(rows)
    row = next(rows, None)
    current: dict[bytes, int | None] = {}
    series: dict[bytes, Series] = {}
    for sample in samples:
        while row is not None and row[0] == sample.id:
            _, path, rbytes, deleted = row
            if deleted:
                current[path] = None
            elif rbytes is not None:
                current[path] = rbytes
            row = next(rows, None)
        if (i := point_at.get(sample.id)) is not None:
            for path, value in current.items():
                if (s := series.get(path)) is None:
                    s = series[path] = [None] * len(points)
                s[i] = value
    if row is not None:
        raise ValueError(f"a row of sample {row[0]} is out of sample order")
    return series


def _own(
    path: bytes,
    rbytes: dict[bytes, Series],
    constant: int,
    children: list[bytes],
    n: int,
) -> list[int]:
    """Return PATH's own size per point, unclamped; 0 while it is absent.

    Its changed CHILDREN's values come from RBYTES; CONSTANT is the sum of
    the others. Absent children count as 0.
    """
    mine = rbytes.get(path, [None] * n)
    kids = [rbytes.get(c, [None] * n) for c in children]
    return [
        0 if mine[i] is None else mine[i] - constant - sum(k[i] or 0 for k in kids)
        for i in range(n)
    ]


def _running_median(values: list[int]) -> list[float]:
    """Return the 3-point running median of VALUES; the ends stay as they are."""
    if len(values) < 3:
        return list(values)
    inner = [median(values[i - 1 : i + 2]) for i in range(1, len(values) - 1)]
    return [values[0], *inner, values[-1]]


def _range(values: list[float]) -> float:
    return max(values) - min(values) if values else 0
