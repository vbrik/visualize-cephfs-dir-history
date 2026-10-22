import json
import os
import stat
from datetime import UTC, datetime

import psycopg
import pytest

from dirhistory import generate
from dirhistory.db import Tree
from dirhistory.generate import (
    index_entry,
    merged_index,
    parse_size,
    parser,
    select_trees,
    tree_document,
    write_json,
)
from dirhistory.history import ROOT, TreeHistory, Unit

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
    assert index_entry(TREE, HISTORY) == {
        "id": 2,
        "label": "/npx/ana",
        "detail": "cluster ad1bf53c, default filesystem, depth 5",
        "file": "tree-2.json",
        "last_sample": "2026-10-21T20:00:00Z",
        "units": 2,
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
