import json
import os
import stat
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from dirhistory import generate
from dirhistory.db import Tree
from dirhistory.generate import (
    format_duration,
    index_entry,
    merged_index,
    parse_duration,
    parse_size,
    parser,
    select_trees,
    tree_document,
    watch_warnings,
    write_json,
)
from dirhistory.history import ROOT, Resolution, Sample, TreeHistory, Unit
from dirhistory.watch import Watched, parse_pattern

TREE = Tree(2, "ad1bf53c-a6ca-11ec-b47e-b04f13b8e306", None, b"/npx/ana", 5)
T1 = datetime(2026, 10, 21, 19, 19, 45, 208826, tzinfo=UTC)
T2 = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
CAFE = b"caf\xe9/x"  # not UTF-8
HISTORY = TreeHistory(
    [T1, T2],
    {ROOT: [100, 200], b"caf\xe9": [50, None], CAFE: [10, None]},
    {CAFE: Unit([10, 0], True), b"caf\xe9": Unit([40, 0], False)},
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1024", 1024),
        ("10GiB", 10 * 2**30),
        ("1.5TiB", int(1.5 * 2**40)),
        ("2 kib", 2048),
        ("0", 0),
    ],
)
def test_parse_size(text, expected):
    assert parse_size(text) == expected


@pytest.mark.parametrize("text", ["", "GiB", "10GB", "-1", "1.5"])
def test_parse_size_rejects(text):
    with pytest.raises(ValueError, match=text or "''"):
        parse_size(text)


def test_select_trees_defaults_to_all():
    other = Tree(1, "x", "default", b"/exp", 5)
    assert select_trees([other, TREE], []) == [other, TREE]


def test_select_trees_names_unknown_ids():
    with pytest.raises(ValueError, match=r"\b7\b.*\b9\b"):
        select_trees([TREE], [9, 2, 7])


def test_tree_document_round_trip(tmp_path):
    doc = tree_document(TREE, HISTORY, 10, T2)
    path = tmp_path / "tree-2.json"
    write_json(path, doc)
    loaded = json.loads(path.read_text(encoding="ascii"))

    assert loaded["tree"] == {
        "id": 2,
        "fs_path": "/npx/ana",
        "fs_name": None,
        "cluster": "ad1bf53c-a6ca-11ec-b47e-b04f13b8e306",
        "max_depth": 5,
    }
    assert loaded["threshold"] == 10
    assert loaded["generated"] == "2026-10-21T20:00:00Z"
    assert loaded["times"] == ["2026-10-21T19:19:45Z", "2026-10-21T20:00:00Z"]
    assert {os.fsencode(p): v for p, v in loaded["rbytes"].items()} == HISTORY.rbytes
    units = {os.fsencode(p): v for p, v in loaded["units"].items()}
    assert units == {
        CAFE: {"own": [10, 0], "leaf": True},
        b"caf\xe9": {"own": [40, 0], "leaf": False},
    }


def test_write_json_is_world_readable_and_leaves_no_temp_files(tmp_path):
    path = tmp_path / "trees.json"
    write_json(path, [])
    write_json(path, [1])
    assert json.loads(path.read_text()) == [1]
    assert stat.S_IMODE(path.stat().st_mode) & 0o044 == 0o044
    assert [p.name for p in tmp_path.iterdir()] == ["trees.json"]


def test_index_entry():
    assert index_entry(TREE, HISTORY, T2) == {
        "id": 2,
        "label": "/npx/ana",
        "detail": "cluster ad1bf53c, default filesystem, depth 5",
        "file": "tree-2.json",
        "last_sample": "2026-10-21T20:00:00Z",
        "generated": "2026-10-21T20:00:00Z",
        "units": 2,
        "watched": 0,
    }


def test_merged_index_keeps_other_live_trees_and_drops_gone_ones(tmp_path):
    old = [{"id": 1, "v": "old"}, {"id": 2, "v": "old"}, {"id": 3, "v": "old"}]
    write_json(tmp_path / "trees.json", old)
    live = [Tree(i, "x", None, b"/t", 1) for i in (1, 2)]
    merged = merged_index(tmp_path, [{"id": 2, "v": "new"}], live)
    assert merged == [{"id": 1, "v": "old"}, {"id": 2, "v": "new"}]


def test_merged_index_without_existing_index(tmp_path):
    assert merged_index(tmp_path, [{"id": 5}], [Tree(5, "x", None, b"/t", 1)]) == [
        {"id": 5}
    ]


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_max_units_must_be_positive(value, capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["--max-units", value])
    assert "--max-units" in capsys.readouterr().err


def test_threshold_must_be_positive(capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["--threshold", "0"])
    assert "--threshold" in capsys.readouterr().err


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_unwritable_out_is_an_error_not_a_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: _Conn())
    monkeypatch.setattr(generate.db, "check_schema", lambda conn: None)
    monkeypatch.setattr(generate.db, "trees", lambda conn: [TREE])
    blocker = tmp_path / "file"
    blocker.write_text("")
    assert generate.main(["--db", "x", "--out", str(blocker / "data")]) == 1
    assert capsys.readouterr().err.startswith("ERROR: ")


def test_database_errors_are_errors_not_tracebacks(monkeypatch, capsys):
    def denied(conn):
        raise psycopg.errors.InsufficientPrivilege("permission denied for schema")

    monkeypatch.setattr(generate.db, "connect", lambda dsn: _Conn())
    monkeypatch.setattr(generate.db, "check_schema", denied)
    assert generate.main(["--db", "x"]) == 1
    assert "ERROR: " in capsys.readouterr().err


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("7d", timedelta(days=7)),
        ("12h", timedelta(hours=12)),
        ("15m", timedelta(minutes=15)),
        ("2w", timedelta(weeks=2)),
        ("0", timedelta(0)),
    ],
)
def test_parse_duration(text, expected):
    assert parse_duration(text) == expected


@pytest.mark.parametrize("text", ["", "7", "d", "1.5d", "-1d", "7days"])
def test_parse_duration_rejects(text):
    with pytest.raises(ValueError, match="duration"):
        parse_duration(text)


@pytest.mark.parametrize(
    "argv",
    [
        ["--recent", "30d"],  # 721 hourly points > 400
        ["--min-bucket", "0"],
        ["--max-points", "1"],
    ],
)
def test_resolution_options_are_checked(argv, capsys):
    with pytest.raises(SystemExit):
        generate.main(["--db", "x", *argv])
    assert "error:" in capsys.readouterr().err


def test_resolution_options_reach_summarize():
    args = parser().parse_args(
        ["--recent", "2d", "--max-points", "100", "--min-bucket", "6h"]
    )
    assert generate.resolution(args) == Resolution(
        recent=timedelta(days=2), max_points=100, min_bucket=timedelta(hours=6)
    )


@pytest.mark.parametrize(
    "d", [timedelta(0), timedelta(days=7), timedelta(hours=36), timedelta(seconds=90)]
)
def test_format_duration_round_trips(d):
    assert parse_duration(format_duration(d)) == d


def test_tree_document_and_index_entry_carry_watched():
    h = TreeHistory(
        HISTORY.times,
        HISTORY.rbytes,
        HISTORY.units,
        Watched([b"caf\xe9", ROOT], [2], 0),
    )
    assert tree_document(TREE, h, 10, T2)["watched"] == ["caf\udce9", "."]
    assert index_entry(TREE, h, T2)["watched"] == 2


def test_tree_document_without_watch_has_empty_list():
    assert tree_document(TREE, HISTORY, 10, T2)["watched"] == []


def test_watch_warnings():
    ps = [parse_pattern(t) for t in ("/a/x", "/b/*", "/c")]
    assert watch_warnings(ps, [True, True, False], [0, 3, 0], all_trees=True) == [
        "WARNING: watch pattern '/a/x' matches no directory",
        "WARNING: watch pattern '/c' applies to no tree",
    ]
    assert watch_warnings(ps, [True, True, False], [0, 3, 0], all_trees=False) == [
        "WARNING: watch pattern '/a/x' matches no directory",
    ]


def test_bad_watch_file_is_an_error_before_the_database(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: pytest.fail("connected"))
    f = tmp_path / "w.toml"
    f.write_text('watched = ["/a"]\n')
    assert generate.main(["--db", "x", "--watch", str(f)]) == 1
    assert capsys.readouterr().err.startswith("ERROR: ")


def test_missing_watch_file_is_an_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: pytest.fail("connected"))
    assert generate.main(["--db", "x", "--watch", str(tmp_path / "nope.toml")]) == 1
    assert "nope.toml" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_max_watched_must_be_positive(value, capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["--max-watched", value])
    assert "--max-watched" in capsys.readouterr().err


class _TxConn(_Conn):
    def transaction(self):
        return _Conn()


def _fake_db(monkeypatch, trees):
    """Serve TREES ({Tree: [(path, rbytes)] of one sample}) as the database."""
    rows = {t.id: r for t, r in trees.items()}
    monkeypatch.setattr(generate.db, "connect", lambda dsn: _TxConn())
    monkeypatch.setattr(generate.db, "check_schema", lambda conn: None)
    monkeypatch.setattr(generate.db, "trees", lambda conn: list(trees))
    monkeypatch.setattr(generate.db, "samples", lambda conn, tid: [Sample(tid, T1)])
    monkeypatch.setattr(generate.db, "current_errors", lambda conn, tid: 0)
    monkeypatch.setattr(generate.db, "changed_paths", lambda conn, later: set())
    monkeypatch.setattr(generate.db, "sample_rows", lambda conn, sid: iter(rows[sid]))
    monkeypatch.setattr(
        generate.db,
        "path_rows",
        lambda conn, tid, paths: [
            (tid, p, b, False) for p, b in rows[tid] if p in paths
        ],
    )


def test_run_writes_watched_and_warns_relative_to_selected_trees(
    monkeypatch, tmp_path, capsys
):
    exp = Tree(1, "c", None, b"/exp", 2)
    sim = Tree(2, "c", None, b"/sim", 2)
    _fake_db(
        monkeypatch,
        {exp: [(ROOT, 9), (b"a", 5), (b"b", 4)], sim: [(ROOT, 9), (b"s", 9)]},
    )
    watch = tmp_path / "watch.toml"
    watch.write_text('watch = ["/exp/*", "/sim/missing", "/nowhere"]\n')
    out = tmp_path / "data"
    argv = ["--db", "x", "--out", str(out), "--watch", str(watch)]

    assert generate.main([*argv, "--tree", "1"]) == 0
    doc = json.loads((out / "tree-1.json").read_text())
    assert doc["watched"] == ["a", "b"]
    assert doc["rbytes"]["a"] == [5]
    assert json.loads((out / "trees.json").read_text())[0]["watched"] == 2
    assert "WARNING" not in capsys.readouterr().err  # /sim and /nowhere not run

    assert generate.main(argv) == 0
    err = capsys.readouterr().err
    assert "watch pattern '/sim/missing' matches no directory" in err
    assert "watch pattern '/nowhere' applies to no tree" in err


def test_run_warns_when_the_cap_drops_watched_dirs(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    _fake_db(monkeypatch, {exp: [(ROOT, 9), (b"a", 5), (b"b", 4)]})
    watch = tmp_path / "watch.toml"
    watch.write_text('watch = ["/exp/*"]\n')
    argv = ["--db", "x", "--out", str(tmp_path / "d"), "--watch", str(watch)]
    assert generate.main([*argv, "--max-watched", "1"]) == 0
    assert "--max-watched 1 dropped 1 watched" in capsys.readouterr().err
