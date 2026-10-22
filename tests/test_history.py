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

T0 = datetime(2026, 10, 1, tzinfo=UTC)
GiB = 2**30


def samples(n: int, step: timedelta = timedelta(hours=1)) -> list[Sample]:
    return [Sample(i + 1, T0 + i * step) for i in range(n)]


def run(rows, n=None, threshold=1, limit=100, step=timedelta(hours=1)):
    """Summarize a tree given as rows (sample_id, path, rbytes, deleted).

    Rows of sample 1 form the first sample; the rest are later changes.
    """
    n = n or max(r[0] for r in rows)
    first = [(p, b) for s, p, b, _ in rows if s == 1]
    changed = {p for s, p, _, _ in rows if s > 1}

    def fetch(paths):
        return [r for r in rows if r[1] in paths]

    return summarize(samples(n, step), changed, first, fetch, threshold, limit)


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


def test_resolution_reports_points_needed_by_recent_window():
    assert Resolution(recent=timedelta(days=7)).recent_points() == 7 * 24 + 1
    assert Resolution(recent=timedelta(0)).recent_points() == 1


def test_single_sample_tree_has_root_series_and_no_units():
    h = run([(1, ROOT, 100, False), (1, b"a", 60, False)])
    assert h.rbytes == {ROOT: [100]}
    assert h.units == {}


def test_deep_growth_makes_only_the_leaf_a_unit():
    # Growth in a/b/c propagates to a/b, a and ROOT, but only a/b/c owns it.
    rows = [
        (1, ROOT, 1000, False),
        (1, b"a", 700, False),
        (1, b"a/b", 500, False),
        (1, b"a/b/c", 300, False),
        (1, b"a/x", 150, False),
        (1, b"z", 300, False),
        (2, ROOT, 1100, False),
        (2, b"a", 800, False),
        (2, b"a/b", 600, False),
        (2, b"a/b/c", 400, False),
    ]
    h = run(rows)
    assert set(h.units) == {b"a/b/c"}
    assert h.units[b"a/b/c"].own == [300, 400]
    assert h.units[b"a/b/c"].leaf


def test_own_file_growth_makes_a_parent_a_unit():
    rows = [
        (1, ROOT, 100, False),
        (1, b"a", 100, False),
        (1, b"a/b", 40, False),
        (2, ROOT, 150, False),
        (2, b"a", 150, False),
    ]
    h = run(rows)
    assert set(h.units) == {b"a"}
    assert h.units[b"a"].own == [60, 110]
    assert not h.units[b"a"].leaf


def test_own_size_subtracts_changed_and_constant_children():
    rows = [
        (1, ROOT, 100, False),
        (1, b"c1", 30, False),
        (1, b"c2", 50, False),
        (2, ROOT, 130, False),
        (2, b"c1", 60, False),
    ]
    h = run(rows, threshold=1000)
    # ROOT's own files: 100 - 30 - 50 = 20, then 130 - 60 - 50 = 20.
    assert set(h.units) == set()
    h = run(rows, threshold=30)
    assert set(h.units) == {b"c1"}


def test_null_rbytes_keeps_previous_value():
    rows = [
        (1, ROOT, 100, False),
        (1, b"a", 100, False),
        (2, ROOT, None, False),
        (2, b"a", None, False),
        (3, ROOT, 200, False),
        (3, b"a", 200, False),
    ]
    h = run(rows)
    assert h.units[b"a"].own == [100, 100, 200]
    assert h.rbytes[ROOT] == [100, 100, 200]


def test_new_dir_range_counts_from_zero():
    rows = [
        (1, ROOT, 100, False),
        (2, ROOT, 150, False),
        (2, b"new", 50, False),
    ]
    h = run(rows, threshold=50)
    assert set(h.units) == {b"new"}
    assert h.units[b"new"].own == [0, 50]


def test_deleted_then_recreated_dir():
    rows = [
        (1, ROOT, 100, False),
        (1, b"d", 80, False),
        (2, ROOT, 20, False),
        (2, b"d", None, True),
        (3, ROOT, 30, False),
        (3, b"d", 10, False),
    ]
    h = run(rows)
    assert h.units[b"d"].own == [80, 0, 10]


def test_parent_lag_within_a_bucket_is_not_a_unit():
    # Sample 2: the child already grew, the parent has not caught up, so the
    # parent's own size dips for one sample. Samples 2 and 3 share an hour
    # bucket, and only the last sample of a bucket is used.
    rows = [
        (1, ROOT, 1000 * GiB, False),
        (1, b"p", 1000 * GiB, False),
        (1, b"p/c", 400 * GiB, False),
        (2, b"p/c", 500 * GiB, False),
        (3, ROOT, 1100 * GiB, False),
        (3, b"p", 1100 * GiB, False),
    ]
    times = [T0, T0 + timedelta(hours=1), T0 + timedelta(hours=1, minutes=5)]
    s = [Sample(i + 1, t) for i, t in enumerate(times)]
    first = [(p, b) for i, p, b, _ in rows if i == 1]
    changed = {p for i, p, _, _ in rows if i > 1}
    h = summarize(
        s, changed, first, lambda ps: [r for r in rows if r[1] in ps], 10 * GiB, 100
    )
    assert set(h.units) == {b"p/c"}


def test_parent_lag_across_buckets_is_not_a_unit():
    # The parent catches up one bucket late: its own size dips at one point.
    rows = [
        (1, ROOT, 1000, False),
        (1, b"p", 1000, False),
        (1, b"p/c", 400, False),
        (2, b"p/c", 500, False),
        (3, ROOT, 1100, False),
        (3, b"p", 1100, False),
    ]
    h = run(rows, n=4, threshold=50)
    assert set(h.units) == {b"p/c"}
    assert h.units[b"p/c"].own == [400, 500, 500, 500]


def test_units_plus_rest_equal_root():
    rows = [
        (1, ROOT, 1000, False),
        (1, b"a", 600, False),
        (1, b"a/b", 200, False),
        (1, b"c", 300, False),
        (2, ROOT, 1300, False),
        (2, b"a", 800, False),
        (2, b"a/b", 300, False),
        (2, b"c", 400, False),
    ]
    h = run(rows)
    assert set(h.units) == {b"a", b"a/b", b"c"}
    for i in range(2):
        units = sum(u.own[i] for u in h.units.values())
        root_own = h.rbytes[ROOT][i] - h.rbytes[b"a"][i] - h.rbytes[b"c"][i]
        assert units + root_own == h.rbytes[ROOT][i]


def test_negative_own_is_clamped_to_zero():
    # Children reported larger than their parent (propagation lag).
    rows = [
        (1, ROOT, 100, False),
        (1, b"a", 100, False),
        (1, b"a/b", 50, False),
        (2, ROOT, 100, False),
        (2, b"a", 100, False),
        (2, b"a/b", 180, False),
    ]
    h = run(rows)
    assert h.units[b"a"].own == [50, 0]


def test_rbytes_cover_root_and_ancestors_of_units():
    rows = [
        (1, ROOT, 100, False),
        (1, b"a", 100, False),
        (1, b"a/b", 100, False),
        (1, b"a/b/c", 10, False),
        (2, ROOT, 200, False),
        (2, b"a", 200, False),
        (2, b"a/b", 200, False),
        (2, b"a/b/c", 110, False),
    ]
    h = run(rows, threshold=100)
    assert set(h.units) == {b"a/b/c"}
    assert set(h.rbytes) == {ROOT, b"a", b"a/b", b"a/b/c"}


@pytest.mark.parametrize(("limit", "expected"), [(1, {b"big"}), (2, {b"big", b"mid"})])
def test_unit_limit_keeps_largest_ranges(limit, expected):
    rows = [
        (1, ROOT, 0, False),
        (2, ROOT, 60, False),
        (2, b"big", 30, False),
        (2, b"mid", 20, False),
        (2, b"small", 10, False),
    ]
    assert set(run(rows, limit=limit).units) == expected


def test_times_are_the_chosen_samples():
    rows = [(1, ROOT, 1, False), (3, ROOT, 3, False)]
    h = run(rows)
    assert h.times == [T0, T0 + timedelta(hours=1), T0 + timedelta(hours=2)]
    assert h.rbytes[ROOT] == [1, 1, 3]


def test_one_point_spike_of_a_leaf_is_a_unit():
    # Only dirs with changed children can show lag dips, so a leaf's own
    # series is not smoothed: a dataset that lived one point still counts.
    rows = [
        (1, ROOT, 10, False),
        (1, b"a", 10, False),
        (1, b"a/x", 10, False),
        (2, ROOT, 15, False),
        (2, b"a", 15, False),
        (2, b"a/x", 15, False),
        (3, ROOT, 10, False),
        (3, b"a", 10, False),
        (3, b"a/x", 10, False),
    ]
    h = run(rows, threshold=5)
    assert set(h.units) == {b"a/x"}


def test_rows_are_read_in_one_pass():
    rows = [
        (1, ROOT, 100, False),
        (1, b"a", 100, False),
        (2, ROOT, 200, False),
        (2, b"a", 200, False),
    ]
    first = [(p, b) for s, p, b, _ in rows if s == 1]

    def fetch(paths):
        return (r for r in rows if r[1] in paths)  # an iterator: one pass only

    h = summarize(samples(2), {ROOT, b"a"}, first, fetch, 1, 10)
    assert h.units[b"a"].own == [100, 200]


def test_rows_out_of_sample_order_are_rejected():
    rows = [(2, ROOT, 200, False), (1, ROOT, 100, False)]
    with pytest.raises(ValueError, match="sample 1"):
        summarize(samples(2), {ROOT}, [(ROOT, 100)], lambda paths: rows, 1, 10)
