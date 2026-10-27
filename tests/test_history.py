from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from dirhistory.history import (
    MAX_POINTS,
    ROOT,
    Resolution,
    Sample,
    ancestors,
    choose_points,
    summarize,
)
from dirhistory.sets import DirGroup, DirSet, TooManyDirs, parse_pattern

T0 = datetime(2026, 10, 1, tzinfo=UTC)
GiB = 2**30


def samples(n: int, step: timedelta = timedelta(hours=1)) -> list[Sample]:
    return [Sample(i + 1, T0 + i * step) for i in range(n)]


def ds(name: str, *pairs: tuple[str, int]) -> DirSet:
    """The set NAME with one group per (pattern text, depth) of PAIRS."""
    return DirSet(name, [DirGroup([parse_pattern(t)], d) for t, d in pairs])


def run(rows, n=None, step=timedelta(hours=1), sets=(), cap=100):
    """Summarize a tree given as rows (sample_id, path, rbytes, deleted),
    storing the dirs of SETS (DirSets), at most CAP.

    Rows of sample 1 form the first sample; the rest are later changes.
    """
    n = n or max(r[0] for r in rows)
    first = [p for s, p, _, _ in rows if s == 1]
    changed = {p for s, p, _, _ in rows if s > 1}

    def fetch(paths):
        return [r for r in rows if r[1] in paths]

    return summarize(samples(n, step), changed, first, fetch, sets=sets, max_dirs=cap)


def test_ancestors_end_at_root():
    assert list(ancestors(b"a/b/c")) == [b"a/b", b"a", ROOT]
    assert list(ancestors(b"a")) == [ROOT]
    assert list(ancestors(ROOT)) == []


def test_choose_points_keeps_every_sample_when_few():
    s = samples(5)
    assert choose_points(s) == s


def test_choose_points_keeps_last_sample_per_bucket_and_latest():
    s = samples(2000, timedelta(minutes=5))  # ~7 days of 5-min samples
    points = choose_points(s)
    assert len(points) <= MAX_POINTS
    assert points[-1] == s[-1]
    gaps = {b.started - a.started for a, b in pairwise(points[:-1])}
    assert gaps == {timedelta(hours=1)}
    assert all(p.started.minute == 55 for p in points[:-1])


def test_choose_points_collapses_samples_within_an_hour():
    s = samples(12, timedelta(minutes=5))
    assert choose_points(s) == [s[-1]]


def test_choose_points_widens_buckets_for_long_histories():
    s = samples(24 * 60, timedelta(hours=1))  # 60 days of hourly samples
    points = choose_points(s, Resolution(recent=timedelta(0)))
    assert len(points) == 60 * 4  # 6-hour buckets: hourly would exceed MAX_POINTS
    assert points[-1] == s[-1]


def test_choose_points_keeps_recent_history_at_the_finest_buckets():
    s = samples(24 * 60, timedelta(hours=1))  # 60 days of hourly samples
    points = choose_points(s, Resolution(recent=timedelta(days=7)))
    assert len(points) <= MAX_POINTS
    assert points[-1] == s[-1]
    recent = [p for p in points if p.started > s[-1].started - timedelta(days=7)]
    assert len(recent) == 7 * 24
    older = points[: -len(recent)]
    gaps = {b.started - a.started for a, b in pairwise(older)}
    assert gaps == {timedelta(hours=6)}  # 53 days at 6 h + 168 recent = 380 points


def test_choose_points_honors_max_points_and_min_bucket():
    s = samples(48, timedelta(minutes=5))  # 4 hours
    assert len(choose_points(s, Resolution(min_bucket=timedelta(minutes=15)))) == 16
    res = Resolution(recent=timedelta(0), max_points=2)
    assert len(choose_points(samples(48, timedelta(hours=1)), res)) == 2


def test_choose_points_coarsens_past_weekly_buckets_to_fit():
    s = samples(3, timedelta(weeks=1))
    points = choose_points(s, Resolution(recent=timedelta(0), max_points=2))
    assert len(points) == 2  # 2-week buckets
    assert points[-1] == s[-1]


def test_choose_points_stays_within_max_points_for_any_history():
    s = samples(8 * 365, timedelta(days=1))  # 8 years: weekly buckets need 417
    points = choose_points(s)
    assert len(points) <= MAX_POINTS
    assert points[-1] == s[-1]
    older = [p for p in points if p.started <= s[-1].started - timedelta(days=7)]
    gaps = {b.started - a.started for a, b in pairwise(older[:-1])}
    assert gaps == {timedelta(weeks=2)}  # the smallest multiple of 1 w that fits


def test_resolution_reports_points_needed_by_recent_window():
    assert Resolution(recent=timedelta(days=7)).recent_points() == 7 * 24 + 1
    assert Resolution(recent=timedelta(0)).recent_points() == 1


def test_null_rbytes_keeps_previous_value():
    rows = [
        (1, ROOT, 100, False),
        (1, b"a", 100, False),
        (2, ROOT, None, False),
        (2, b"a", None, False),
        (3, ROOT, 200, False),
        (3, b"a", 200, False),
    ]
    h = run(rows, sets=[ds("s", ("a", 0), (".", 0))])
    assert h.rbytes[b"a"] == [100, 100, 200]
    assert h.rbytes[ROOT] == [100, 100, 200]


def test_times_are_the_chosen_samples():
    rows = [(1, ROOT, 1, False), (3, ROOT, 3, False)]
    h = run(rows, sets=[ds("s", (".", 0))])
    assert h.times == [T0, T0 + timedelta(hours=1), T0 + timedelta(hours=2)]
    assert h.rbytes[ROOT] == [1, 1, 3]


def test_rows_are_read_in_one_pass():
    rows = [
        (1, ROOT, 100, False),
        (1, b"a", 100, False),
        (2, ROOT, 200, False),
        (2, b"a", 200, False),
    ]

    def fetch(paths):
        return (r for r in rows if r[1] in paths)  # an iterator: one pass only

    h = summarize(
        samples(2), {ROOT, b"a"}, iter([ROOT, b"a"]), fetch, sets=[ds("s", ("a", 0))]
    )
    assert h.rbytes[b"a"] == [100, 200]


def test_rows_out_of_sample_order_are_rejected():
    rows = [(2, b"a", 200, False), (1, b"a", 100, False)]
    with pytest.raises(ValueError, match="sample 1"):
        summarize(
            samples(2), {b"a"}, [b"a"], lambda paths: rows, sets=[ds("s", ("a", 0))]
        )


def test_stored_dirs_reach_depth_below_each_set_dir():
    rows = [
        (1, ROOT, 9, False),
        (1, b"a", 5, False),
        (1, b"a/x", 3, False),
        (1, b"a/x/y", 1, False),
        (1, b"b", 4, False),
    ]
    h = run(rows, sets=[ds("s", ("a", 1))])
    assert set(h.rbytes) == {b"a", b"a/x"}
    assert h.split == [b"a"]


def test_split_needs_all_subdirs_stored():
    # Set t stores a/x only, and a is in set s at depth 0: a is not split.
    rows = [
        (1, ROOT, 9, False),
        (1, b"a", 5, False),
        (1, b"a/x", 3, False),
        (1, b"a/y", 2, False),
    ]
    h = run(rows, sets=[ds("s", ("a", 0)), ds("t", ("a/x", 0))])
    assert h.split == []
    assert set(h.rbytes) == {b"a", b"a/x"}


def test_depth_of_another_set_makes_a_dir_splittable():
    rows = [(1, ROOT, 9, False), (1, b"a", 5, False), (1, b"a/x", 3, False)]
    h = run(rows, sets=[ds("s", ("a", 0)), ds("t", ("a", 1))])
    assert h.split == [b"a"]


def test_split_is_sorted_and_includes_deeper_levels():
    rows = [(1, ROOT, 9, False)] + [
        (1, p, 1, False) for p in (b"b", b"b/x", b"a", b"a/y", b"a/y/z")
    ]
    h = run(rows, sets=[ds("s", ("*", 2))])
    assert h.split == [b"a", b"a/y", b"b"]


def test_unchanged_set_dir_gets_a_constant_series():
    rows = [
        (1, ROOT, 100, False),
        (1, b"p", 40, False),
        (1, b"p/q", 30, False),
        (1, b"z", 10, False),
        (2, ROOT, 110, False),
        (2, b"z", 20, False),
    ]
    h = run(rows, sets=[ds("s", ("p/*", 0))])
    assert h.sets[0].paths == [b"p/q"]
    assert h.rbytes == {b"p/q": [30, 30]}


def test_deleted_set_dir_turns_absent():
    rows = [
        (1, ROOT, 100, False),
        (1, b"d", 50, False),
        (2, ROOT, 50, False),
        (2, b"d", None, True),
    ]
    h = run(rows, sets=[ds("s", ("d", 0))])
    assert h.sets[0].paths == [b"d"]
    assert h.rbytes[b"d"] == [50, None]


def test_dir_created_later_is_stored():
    rows = [
        (1, ROOT, 100, False),
        (2, ROOT, 150, False),
        (2, b"new", 50, False),
        (2, b"new/x", 20, False),
    ]
    h = run(rows, sets=[ds("s", ("*", 1))])
    assert h.sets[0].paths == [b"new"]
    assert h.rbytes == {b"new": [None, 50], b"new/x": [None, 20]}
    assert h.split == [b"new"]


def test_set_dirs_order_and_found():
    rows = [(1, ROOT, 9, False)] + [(1, p, 1, False) for p in (b"a", b"b", b"c")]
    h = run(rows, n=1, sets=[ds("s", ("c", 0), ("*", 0), ("z", 0))], cap=3)
    assert h.sets[0].paths == [b"c", b"a", b"b"]
    assert h.sets[0].found == [1, 3, 0]


def test_sets_are_separate():
    rows = [(1, ROOT, 9, False)] + [
        (1, p, 1, False) for p in (b"a", b"a/x", b"b", b"b/y", b"c")
    ]
    h = run(
        rows,
        n=1,
        sets=[
            ds("Top", ("*", 0)),
            ds("Second", ("*/*", 0), ("a", 0)),
            ds("None", ("q", 0)),
        ],
    )
    assert [(s.name, s.paths, s.found) for s in h.sets] == [
        ("Top", [b"a", b"b", b"c"], [3]),
        ("Second", [b"a/x", b"b/y", b"a"], [2, 1]),  # a is in both sets
        ("None", [], [0]),
    ]
    assert h.rbytes[b"a"] == [1]


def test_root_set_dir_splits():
    h = run([(1, ROOT, 9, False), (1, b"a", 5, False)], sets=[ds("r", (".", 1))])
    assert h.sets[0].paths == [ROOT]
    assert h.split == [ROOT]
    assert set(h.rbytes) == {ROOT, b"a"}


def test_too_many_stored_dirs_raises_before_fetching():
    first = [ROOT, b"a", b"b", b"c"]
    with pytest.raises(TooManyDirs) as e:
        summarize(
            samples(1),
            set(),
            first,
            lambda paths: pytest.fail("fetched"),
            sets=[ds("ok", ("a", 0)), ds("Users", ("*", 0))],
            max_dirs=2,
        )
    assert (e.value.found, e.value.counts) == (3, [("ok", 1), ("Users", 3)])


def test_stored_count_is_distinct_paths_across_sets():
    rows = [(1, ROOT, 9, False), (1, b"a", 1, False), (1, b"b", 1, False)]
    h = run(rows, sets=[ds("s", ("a", 0)), ds("t", ("a", 0), ("b", 0))], cap=2)
    assert set(h.rbytes) == {b"a", b"b"}


def test_no_sets_stores_nothing():
    h = run([(1, ROOT, 9, False), (2, ROOT, 10, False)])
    assert h.rbytes == {} and h.sets == [] and h.split == [] and len(h.times) == 2
