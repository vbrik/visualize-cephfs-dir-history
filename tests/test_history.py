from datetime import UTC, datetime, timedelta
from itertools import pairwise

import pytest

from dirhistory.dirsets import DirGroup, DirSet, parse_pattern
from dirhistory.history import (
    MAX_POINTS,
    ROOT,
    Resolution,
    Sample,
    choose_points,
    summarize,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)
GiB = 2**30


def samples(n: int, step: timedelta = timedelta(hours=1)) -> list[Sample]:
    return [Sample(i + 1, T0 + i * step) for i in range(n)]


def ds(name: str, *pairs: tuple[str, int]) -> DirSet:
    """The dirset NAME with one group per (pattern text, depth) of PAIRS."""
    return DirSet(name, [DirGroup([parse_pattern(t)], d) for t, d in pairs])


def run(rows, n=None, step=timedelta(hours=1), dirsets=(), budget=None):
    """Summarize a tree given as rows (sample_id, path, rbytes, rentries, deleted),
    storing the dirs of DIRSETS (DirSets) within BUDGET (None: all).

    Rows of sample 1 form the first sample; the rest are later changes.
    """
    n = n or max(r[0] for r in rows)
    first = [p for s, p, *_ in rows if s == 1]
    changed = {p for s, p, *_ in rows if s > 1}

    def fetch(paths):
        return [r for r in rows if r[1] in paths]

    return summarize(
        samples(n, step), changed, first, fetch, dirsets=dirsets, budget=budget
    )


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
    points = choose_points(s, Resolution(max_points=400))
    assert len(points) == 60 * 4  # 6-hour buckets: hourly would exceed 400
    assert points[-1] == s[-1]


def test_choose_points_keeps_recent_history_at_the_finest_buckets():
    s = samples(24 * 60, timedelta(hours=1))  # 60 days of hourly samples
    points = choose_points(s, Resolution(recent=timedelta(days=7), max_points=400))
    assert len(points) <= 400
    assert points[-1] == s[-1]
    recent = [p for p in points if p.started > s[-1].started - timedelta(days=7)]
    assert len(recent) == 7 * 24
    older = points[: -len(recent)]
    gaps = {b.started - a.started for a, b in pairwise(older)}
    assert gaps == {timedelta(hours=6)}  # 53 days at 6 h + 168 recent = 380 points


def test_choose_points_honors_max_points():
    res = Resolution(max_points=2)
    assert len(choose_points(samples(48, timedelta(hours=1)), res)) == 2


def test_choose_points_coarsens_past_weekly_buckets_to_fit():
    s = samples(3, timedelta(weeks=1))
    points = choose_points(s, Resolution(max_points=2))
    assert len(points) == 2  # 2-week buckets
    assert points[-1] == s[-1]


def test_choose_points_stays_within_max_points_for_any_history():
    s = samples(8 * 365, timedelta(days=1))  # 8 years: weekly buckets need 417
    res = Resolution(recent=timedelta(days=7), max_points=400)
    points = choose_points(s, res)
    assert len(points) <= 400
    assert points[-1] == s[-1]
    older = [p for p in points if p.started <= s[-1].started - timedelta(days=7)]
    gaps = {b.started - a.started for a, b in pairwise(older[:-1])}
    assert gaps == {timedelta(weeks=2)}  # the smallest multiple of 1 w that fits


def test_choose_points_by_default_samples_years_uniformly_within_max_points():
    s = samples(4 * 365 * 24, timedelta(hours=1))  # 4 years of hourly samples
    points = choose_points(s)
    assert len(points) <= MAX_POINTS
    assert points[-1] == s[-1]
    gaps = {b.started - a.started for a, b in pairwise(points[:-1])}
    assert gaps == {timedelta(days=1)}  # 6-hour buckets would need 5841


def test_resolution_reports_points_needed_by_recent_window():
    assert Resolution(recent=timedelta(days=7)).recent_points() == 7 * 24 + 1
    assert Resolution(recent=timedelta(0)).recent_points() == 1


def test_null_rbytes_keeps_previous_value():
    rows = [
        (1, ROOT, 100, 100, False),
        (1, b"a", 100, 100, False),
        (2, ROOT, None, None, False),
        (2, b"a", None, None, False),
        (3, ROOT, 200, 200, False),
        (3, b"a", 200, 200, False),
    ]
    h = run(rows, dirsets=[ds("s", ("a", 0), (".", 0))])
    assert h.rbytes[b"a"] == [100, 100, 200]
    assert h.rbytes[ROOT] == [100, 100, 200]


def test_rentries_are_carried_forward_apart_from_rbytes():
    # Sample 2: empty files added (entries only); sample 3: an rstats error
    # on entries only; sample 4: deleted; sample 5: back.
    rows = [
        (1, ROOT, 100, 10, False),
        (1, b"a", 100, 10, False),
        (2, b"a", 100, 15, False),
        (3, b"a", 120, None, False),
        (4, b"a", None, None, True),
        (5, b"a", 7, 2, False),
    ]
    h = run(rows, dirsets=[ds("s", ("a", 0))])
    assert h.rbytes[b"a"] == [100, 100, 120, None, 7]
    assert h.rentries[b"a"] == [10, 15, 15, None, 2]


def test_rentries_have_the_same_dirs_as_rbytes():
    # a/x is stored but has no row: both metrics are absent throughout.
    h = summarize(
        samples(2),
        set(),
        [ROOT, b"a", b"a/x"],
        lambda paths: [(1, b"a", 5, 3, False)],
        dirsets=[ds("s", ("a", 1))],
    )
    assert h.rentries == {b"a": [3, 3], b"a/x": [None, None]}
    assert h.rentries.keys() == h.rbytes.keys()


def test_times_are_the_chosen_samples():
    rows = [(1, ROOT, 1, 1, False), (3, ROOT, 3, 3, False)]
    h = run(rows, dirsets=[ds("s", (".", 0))])
    assert h.times == [T0, T0 + timedelta(hours=1), T0 + timedelta(hours=2)]
    assert h.rbytes[ROOT] == [1, 1, 3]


def test_rows_are_read_in_one_pass():
    rows = [
        (1, ROOT, 100, 100, False),
        (1, b"a", 100, 100, False),
        (2, ROOT, 200, 200, False),
        (2, b"a", 200, 200, False),
    ]

    def fetch(paths):
        return (r for r in rows if r[1] in paths)  # an iterator: one pass only

    h = summarize(
        samples(2), {ROOT, b"a"}, iter([ROOT, b"a"]), fetch, dirsets=[ds("s", ("a", 0))]
    )
    assert h.rbytes[b"a"] == [100, 200]


def test_rows_out_of_sample_order_are_rejected():
    rows = [(2, b"a", 200, 200, False), (1, b"a", 100, 100, False)]
    with pytest.raises(ValueError, match="sample 1"):
        summarize(
            samples(2), {b"a"}, [b"a"], lambda paths: rows, dirsets=[ds("s", ("a", 0))]
        )


def test_stored_dirs_reach_depth_below_each_dirset_dir():
    rows = [
        (1, ROOT, 9, 9, False),
        (1, b"a", 5, 5, False),
        (1, b"a/x", 3, 3, False),
        (1, b"a/x/y", 1, 1, False),
        (1, b"b", 4, 4, False),
    ]
    h = run(rows, dirsets=[ds("s", ("a", 1))])
    assert set(h.rbytes) == {b"a", b"a/x"}
    assert h.split == [b"a"]


def test_split_needs_all_subdirs_stored():
    # DirSet t stores a/x only, and a is in dirset s at depth 0: a is not split.
    rows = [
        (1, ROOT, 9, 9, False),
        (1, b"a", 5, 5, False),
        (1, b"a/x", 3, 3, False),
        (1, b"a/y", 2, 2, False),
    ]
    h = run(rows, dirsets=[ds("s", ("a", 0)), ds("t", ("a/x", 0))])
    assert h.split == []
    assert set(h.rbytes) == {b"a", b"a/x"}


def test_depth_of_another_dirset_makes_a_dir_splittable():
    rows = [(1, ROOT, 9, 9, False), (1, b"a", 5, 5, False), (1, b"a/x", 3, 3, False)]
    h = run(rows, dirsets=[ds("s", ("a", 0)), ds("t", ("a", 1))])
    assert h.split == [b"a"]


def test_split_is_sorted_and_includes_deeper_levels():
    rows = [(1, ROOT, 9, 9, False)] + [
        (1, p, 1, 1, False) for p in (b"b", b"b/x", b"a", b"a/y", b"a/y/z")
    ]
    h = run(rows, dirsets=[ds("s", ("*", 2))])
    assert h.split == [b"a", b"a/y", b"b"]


def test_unchanged_dirset_dir_gets_a_constant_series():
    rows = [
        (1, ROOT, 100, 100, False),
        (1, b"p", 40, 40, False),
        (1, b"p/q", 30, 30, False),
        (1, b"z", 10, 10, False),
        (2, ROOT, 110, 110, False),
        (2, b"z", 20, 20, False),
    ]
    h = run(rows, dirsets=[ds("s", ("p/*", 0))])
    assert h.dirsets[0].paths == [b"p/q"]
    assert h.rbytes == {b"p/q": [30, 30]}


def test_deleted_dirset_dir_turns_absent():
    rows = [
        (1, ROOT, 100, 100, False),
        (1, b"d", 50, 50, False),
        (2, ROOT, 50, 50, False),
        (2, b"d", None, None, True),
    ]
    h = run(rows, dirsets=[ds("s", ("d", 0))])
    assert h.dirsets[0].paths == [b"d"]
    assert h.rbytes[b"d"] == [50, None]


def test_dir_created_later_is_stored():
    rows = [
        (1, ROOT, 100, 100, False),
        (2, ROOT, 150, 150, False),
        (2, b"new", 50, 50, False),
        (2, b"new/x", 20, 20, False),
    ]
    h = run(rows, dirsets=[ds("s", ("*", 1))])
    assert h.dirsets[0].paths == [b"new"]
    assert h.rbytes == {b"new": [None, 50], b"new/x": [None, 20]}
    assert h.split == [b"new"]


def test_dirset_dirs_order_and_found():
    rows = [(1, ROOT, 9, 9, False)] + [(1, p, 1, 1, False) for p in (b"a", b"b", b"c")]
    h = run(rows, n=1, dirsets=[ds("s", ("c", 0), ("*", 0), ("z", 0))], budget=3)
    assert h.dirsets[0].paths == [b"c", b"a", b"b"]
    assert h.dirsets[0].found == [1, 3, 0]


def test_dirsets_are_separate():
    rows = [(1, ROOT, 9, 9, False)] + [
        (1, p, 1, 1, False) for p in (b"a", b"a/x", b"b", b"b/y", b"c")
    ]
    h = run(
        rows,
        n=1,
        dirsets=[
            ds("Top", ("*", 0)),
            ds("Second", ("*/*", 0), ("a", 0)),
            ds("None", ("q", 0)),
        ],
    )
    assert [(s.name, s.paths, s.found) for s in h.dirsets] == [
        ("Top", [b"a", b"b", b"c"], [3]),
        ("Second", [b"a/x", b"b/y", b"a"], [2, 1]),  # a is in both dirsets
        ("None", [], [0]),
    ]
    assert h.rbytes[b"a"] == [1]


def test_root_dirset_dir_splits():
    h = run(
        [(1, ROOT, 9, 9, False), (1, b"a", 5, 5, False)], dirsets=[ds("r", (".", 1))]
    )
    assert h.dirsets[0].paths == [ROOT]
    assert h.split == [ROOT]
    assert set(h.rbytes) == {ROOT, b"a"}
    assert (h.pruned, h.candidates, h.cut_level) == ([], 2, None)


def test_budget_cuts_before_fetching_and_fetches_only_kept_dirs():
    first = [ROOT, b"a", b"a/x", b"a/y", b"b"]
    fetched = []

    def fetch(paths):
        fetched.append(paths)
        return []

    h = summarize(
        samples(1), set(), first, fetch, dirsets=[ds("s", ("*", 1))], budget=3
    )
    # level 1 fits (1 left); a's 2 subdirs do not
    assert fetched == [{b"a", b"b"}]
    assert set(h.rbytes) == {b"a", b"b"}
    assert (h.pruned, h.split, h.candidates, h.cut_level) == ([b"a"], [], 4, 2)


def test_budget_drops_dirset_dirs_below_the_cut():
    rows = [(1, p, 1, 1, False) for p in (ROOT, b"a", b"b", b"a/x", b"b/y")]
    h = run(rows, dirsets=[ds("Top", ("*", 0)), ds("Deep", ("*/*", 0))], budget=3)
    assert [(s.name, s.paths, s.found, s.dropped) for s in h.dirsets] == [
        ("Top", [b"a", b"b"], [2], 0),
        ("Deep", [b"a/x"], [2], 1),
    ]


def test_no_budget_keeps_everything():
    rows = [(1, p, 1, 1, False) for p in (ROOT, b"a", b"a/x", b"b")]
    h = run(rows, dirsets=[ds("s", ("*", 1))])
    assert set(h.rbytes) == {b"a", b"a/x", b"b"}
    assert (h.pruned, h.cut_level) == ([], None)


def test_stored_count_is_distinct_paths_across_dirsets():
    rows = [(1, ROOT, 9, 9, False), (1, b"a", 1, 1, False), (1, b"b", 1, 1, False)]
    h = run(rows, dirsets=[ds("s", ("a", 0)), ds("t", ("a", 0), ("b", 0))], budget=2)
    assert set(h.rbytes) == {b"a", b"b"}


def test_no_dirsets_stores_nothing():
    h = run([(1, ROOT, 9, 9, False), (2, ROOT, 10, 10, False)])
    assert h.rbytes == {} and h.dirsets == [] and h.split == [] and len(h.times) == 2
