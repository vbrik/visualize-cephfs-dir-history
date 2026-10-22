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
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

ROOT = b"."

# At most this many time points per tree, so a chart stays readable.
MAX_POINTS = 400

# Candidate time-bucket widths, finest first. The finest is an hour, not
# trackdir's typical 5-minute sampling interval: a parent's rbytes can lag its
# children's within a sample, and keeping only the last sample of each hour
# hides most such lag.
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
    leaf: whether it never had subdirectories, so own is its rbytes.
    """

    own: list[int]
    leaf: bool


@dataclass(frozen=True, slots=True)
class TreeHistory:
    """A tree's chosen time points, its units, and the rbytes series of ROOT,
    the units and their ancestors."""

    times: list[datetime]
    rbytes: dict[bytes, Series]
    units: dict[bytes, Unit]


def parent(path: bytes) -> bytes:
    """Return the parent of PATH, which must not be ROOT."""
    head, sep, _ = path.rpartition(b"/")
    return head if sep else ROOT


def ancestors(path: bytes) -> Iterator[bytes]:
    """Yield the ancestors of PATH, nearest first, ending with ROOT."""
    while path != ROOT:
        path = parent(path)
        yield path


def choose_points(samples: Sequence[Sample]) -> list[Sample]:
    """Return the last sample of each time bucket, oldest first.

    SAMPLES are ordered by (started, id). Buckets are UTC-aligned and of the
    finest BUCKET_WIDTHS width that yields at most MAX_POINTS points (else
    the coarsest). The latest sample is always the last point.
    """
    for width in BUCKET_WIDTHS:
        seconds = width.total_seconds()
        last: dict[int, Sample] = {}
        for sample in samples:
            last[int(sample.started.timestamp() // seconds)] = sample
        if len(last) <= MAX_POINTS:
            break
    return list(last.values())


def summarize(
    samples: Sequence[Sample],
    changed: set[bytes],
    first: Iterable[tuple[bytes, int | None]],
    fetch: Callable[[set[bytes]], Iterable[Row]],
    threshold: int,
    limit: int,
) -> TreeHistory:
    """Return the history of a tree with SAMPLES, ordered by (started, id).

    CHANGED holds the paths with a row in any sample after the first. FIRST
    yields (path, rbytes) for the first sample's rows; it may be streamed.
    FETCH(paths) returns every row of PATHS, ordered by sample.

    A path outside CHANGED has its single row in the first sample, so its
    value is constant, and only CHANGED paths and their parents can change
    own size. A unit is such a directory whose own size, as a 3-point running
    median over the chosen points, ranges by at least THRESHOLD bytes; the
    median drops one-point dips from rbytes propagation lag. At most LIMIT
    units are kept, widest range first, ties by path.
    """
    points = choose_points(samples)
    dirs = changed | {parent(p) for p in changed if p != ROOT}
    constant, has_children = _children(first, changed, dirs)
    wanted = {ROOT} | changed | {a for p in changed for a in ancestors(p)}
    rbytes = _replay(samples, points, fetch(wanted))

    children = defaultdict(list)
    for path in changed - {ROOT}:
        children[parent(path)].append(path)
    own = {d: _own(d, rbytes, constant[d], children[d], len(points)) for d in dirs}

    ranges = {d: _range(_running_median(s)) for d, s in own.items()}
    picked = sorted(
        (d for d, r in ranges.items() if r >= threshold), key=lambda d: (-ranges[d], d)
    )[:limit]
    units = {
        d: Unit([max(v, 0) for v in own[d]], d not in has_children)
        for d in sorted(picked)
    }
    kept = {ROOT} | set(units) | {a for u in units for a in ancestors(u)}
    return TreeHistory(
        [p.started for p in points],
        {p: rbytes.get(p, [None] * len(points)) for p in sorted(kept)},
        units,
    )


def _children(
    first: Iterable[tuple[bytes, int | None]], changed: set[bytes], dirs: set[bytes]
) -> tuple[Counter[bytes], set[bytes]]:
    """Return, for DIRS, the summed rbytes of children outside CHANGED, and
    the DIRS that have any child.

    FIRST yields the first sample's (path, rbytes); NULL rbytes count as 0.
    """
    constant: Counter[bytes] = Counter()
    has_children = {parent(p) for p in changed if p != ROOT}
    for path, rbytes in first:
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

    A value holds until the path's next row. A NULL rbytes (an error) keeps
    the previous value; a tombstone makes it None, as does no row yet.
    """
    point_at = {p.id: i for i, p in enumerate(points)}
    by_sample: defaultdict[int, list[Row]] = defaultdict(list)
    for row in rows:
        by_sample[row[0]].append(row)
    current: dict[bytes, int | None] = {}
    series: dict[bytes, Series] = {}
    for sample in samples:
        for _, path, rbytes, deleted in by_sample.get(sample.id, ()):
            if deleted:
                current[path] = None
            elif rbytes is not None:
                current[path] = rbytes
        if (i := point_at.get(sample.id)) is not None:
            for path, value in current.items():
                series.setdefault(path, [None] * len(points))[i] = value
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
