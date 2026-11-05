"""Turn a trackdir tree's samples into per-directory time series.

Pure logic, no database: the caller supplies samples and dir_state rows.

The series kept are two metrics of the *stored* directories, those the
tree's sets cover (sets.Matcher) within its budget (sets.cut): rbytes, and
rentries (rfiles + rsubdirs, so a directory counts itself). The page
derives the bands and cards it draws from them, including a directory's
own-files amount (its value minus its stored subdirectories'), so no own
amounts are computed here.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from dirhistory.sets import (
    ROOT,
    DirSet,
    Matcher,
    SetDirs,
    cut,
    parent,
    select,
)

# Defaults of Resolution.
# At most this many time points per tree, so its file and the page's render
# time stay bounded however long the history grows: years fit at 6 h or 1 d.
MAX_POINTS = 5000
# History this recent keeps the finest buckets. 0: one bucket width for the
# whole history.
RECENT = timedelta(0)
# The finest bucket, fixed: an hour, not trackdir's typical 5-minute sampling
# interval. A parent's rbytes can lag its children's within a sample, which
# shows as a one-point dip in the page's own-files band (the parent's rbytes
# minus its subdirectories'). One sample per hour exposes far fewer lagging
# samples.
MIN_BUCKET = timedelta(hours=1)

# Bucket widths for history older than RECENT, finest first.
BUCKET_WIDTHS = (MIN_BUCKET, *(timedelta(hours=h) for h in (6, 24, 48, 7 * 24)))

# A dir_state row: (sample id, path, rbytes, rentries, deleted), where
# rentries is rfiles + rsubdirs (None if either is NULL)
type Row = tuple[int, bytes, int | None, int | None, bool]
# A series value per chosen time point; None: absent or unknown.
type Series = list[int | None]


@dataclass(frozen=True, slots=True)
class Sample:
    """A trackdir sample of the tree."""

    id: int
    started: datetime


@dataclass(frozen=True, slots=True)
class TreeHistory:
    """A tree's chosen time points, its sets' dirs (in config order), the
    rbytes and rentries series of every stored dir (the same keys), the dirs
    to split, and what the budget cut.

    split: the sorted dirs whose subdirectories are all stored, so the page
    can replace such a dir by them. pruned: the sorted dirs that would be
    split but for the budget (sets.cut). candidates: how many dirs the sets
    cover, stored or not. cut_level: the shallowest level with a covered dir
    left out; None if none is.
    """

    times: list[datetime]
    rbytes: dict[bytes, Series]
    rentries: dict[bytes, Series]
    sets: list[SetDirs]
    split: list[bytes]
    pruned: list[bytes]
    candidates: int
    cut_level: int | None


@dataclass(frozen=True, slots=True)
class Resolution:
    """How finely time points sample a tree's history.

    History within RECENT of the latest sample gets MIN_BUCKET buckets.
    Older history gets the finest of BUCKET_WIDTHS that keeps all points
    within MAX_POINTS, else the smallest whole multiple of the coarsest
    that does.
    """

    recent: timedelta = RECENT
    max_points: int = MAX_POINTS

    def recent_points(self) -> int:
        """Return the most points the recent window can take."""
        return self.recent // MIN_BUCKET + 1


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
    recent = _last_per_bucket([s for s in samples if s.started > cutoff], MIN_BUCKET)
    budget = max(resolution.max_points - len(recent), 1)
    for width in BUCKET_WIDTHS:
        kept = _last_per_bucket(older, width)
        if len(kept) <= budget:
            return kept + recent
    # A span S meets at most S // width + 1 aligned buckets; whole multiples
    # of the coarsest width keep bucket edges stable as history grows.
    span = older[-1].started - older[0].started
    width = (span // (budget * BUCKET_WIDTHS[-1]) + 1) * BUCKET_WIDTHS[-1]
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
    first: Iterable[bytes],
    fetch: Callable[[set[bytes]], Iterable[Row]],
    resolution: Resolution = DEFAULT_RESOLUTION,
    sets: Sequence[DirSet] = (),
    budget: int | None = None,
) -> TreeHistory:
    """Return the history of a tree with SAMPLES, ordered by (started, id).

    CHANGED holds the paths with a row in any sample after the first. FIRST
    yields the paths of the first sample's rows. FETCH(paths) yields every
    row of PATHS, ordered as SAMPLES are. Both are read once, so they may
    stream. RESOLUTION picks the time points.

    The stored dirs are the paths, among the first sample's and CHANGED, that
    SETS cover (sets.Matcher), cut to BUDGET (sets.cut; None: no cut) before
    any series is fetched, so only the kept dirs are. A path outside CHANGED
    has its single row in the first sample, so a never-changed dir gets a
    constant series.

    A dir is split when it is stored with at least one level below it, i.e.
    all its subdirectories are stored too.
    """
    points = choose_points(samples, resolution)
    matcher = Matcher(sets)
    if matcher.wants:
        for path in first:
            matcher.add(path)
        for path in changed:
            matcher.add(path)
    candidates = matcher.stored()
    trimmed = None if budget is None else cut(candidates, budget)
    stored = candidates if trimmed is None else trimmed.kept
    matches = matcher.matches()
    rbytes, rentries = _replay(samples, points, fetch(set(stored)))
    split = {parent(p) for p in stored if p != ROOT and stored.get(parent(p), -1) >= 1}
    absent = [None] * len(points)
    return TreeHistory(
        [p.started for p in points],
        {p: rbytes.get(p, absent[:]) for p in sorted(stored)},
        {p: rentries.get(p, absent[:]) for p in sorted(stored)},
        [select(s.name, m, stored) for s, m in zip(sets, matches, strict=True)],
        sorted(split),
        [] if trimmed is None else trimmed.pruned,
        len(candidates),
        None if trimmed is None else trimmed.level,
    )


def _replay(
    samples: Sequence[Sample], points: Sequence[Sample], rows: Iterable[Row]
) -> tuple[dict[bytes, Series], dict[bytes, Series]]:
    """Return each row path's rbytes and rentries at each of POINTS, carried
    forward.

    ROWS must be ordered as SAMPLES are; they are read once, in one pass. A
    value holds until the path's next row. A NULL value (an error) keeps that
    metric's previous value; a tombstone makes both None, as does no row
    yet. Raises ValueError if a row is out of order or of no sample in
    SAMPLES.
    """
    point_at = {p.id: i for i, p in enumerate(points)}
    rows = iter(rows)
    row = next(rows, None)
    current: dict[bytes, tuple[int | None, int | None]] = {}
    rbytes: dict[bytes, Series] = {}
    rentries: dict[bytes, Series] = {}
    for sample in samples:
        while row is not None and row[0] == sample.id:
            _, path, nbytes, nentries, deleted = row
            if deleted:
                current[path] = (None, None)
            else:
                was_bytes, was_entries = current.get(path, (None, None))
                current[path] = (
                    was_bytes if nbytes is None else nbytes,
                    was_entries if nentries is None else nentries,
                )
            row = next(rows, None)
        if (i := point_at.get(sample.id)) is not None:
            for path, (nbytes, nentries) in current.items():
                if path not in rbytes:
                    rbytes[path] = [None] * len(points)
                    rentries[path] = [None] * len(points)
                rbytes[path][i] = nbytes
                rentries[path][i] = nentries
    if row is not None:
        raise ValueError(f"a row of sample {row[0]} is out of sample order")
    return rbytes, rentries
