import json
import os
import stat
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from dirhistory import generate
from dirhistory.db import Tree
from dirhistory.generate import (
    busiest_hint,
    compact,
    format_duration,
    index_entry,
    merged_index,
    parse_duration,
    parser,
    select_trees,
    set_warnings,
    tree_document,
    tree_sets,
    write_json,
)
from dirhistory.history import ROOT, Resolution, Sample, TreeHistory
from dirhistory.sets import IMPLICIT, DirGroup, DirSet, SetDirs, parse_pattern

TREE = Tree(2, "ad1bf53c-a6ca-11ec-b47e-b04f13b8e306", None, b"/npx/ana", 5)
T1 = datetime(2026, 10, 21, 19, 19, 45, 208826, tzinfo=UTC)
T2 = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
CAFE = b"caf\xe9/x"  # not UTF-8
HISTORY = TreeHistory(
    [T1, T2],
    {ROOT: [100, 200], b"caf\xe9": [50, 50], CAFE: [10, None]},
    {ROOT: [7, 9], b"caf\xe9": [4, 5], CAFE: [3, None]},
    [SetDirs("Café & co", [b"caf\xe9", ROOT], [2]), SetDirs("Empty", [], [])],
    [b"caf\xe9"],
)


def test_compact():
    assert compact([5, 5, 5]) == 5
    assert compact([None, None]) is None
    assert compact([5, 6]) == [5, 6]
    assert compact([None, 5]) == [None, 5]


def test_select_trees_defaults_to_all():
    other = Tree(1, "x", "default", b"/exp", 5)
    assert select_trees([other, TREE], []) == [other, TREE]


def test_select_trees_names_unknown_ids():
    with pytest.raises(ValueError, match=r"\b7\b.*\b9\b"):
        select_trees([TREE], [9, 2, 7])


def test_tree_document_round_trip(tmp_path):
    doc = tree_document(TREE, HISTORY, T2)
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
    assert loaded["generated"] == "2026-10-21T20:00:00Z"
    assert loaded["times"] == ["2026-10-21T19:19:45Z", "2026-10-21T20:00:00Z"]
    assert loaded["metrics"] == ["rbytes", "rentries"]
    assert {os.fsencode(p): v for p, v in loaded["series"].items()} == {
        ROOT: [[100, 200], [7, 9]],
        # rbytes unchanged (one number); rentries changed
        b"caf\xe9": [50, [4, 5]],
        CAFE: [[10, None], [3, None]],
    }
    assert loaded["sets"] == [
        {"name": "Café & co", "paths": ["caf\udce9", "."]},
        {"name": "Empty", "paths": []},
    ]
    assert loaded["split"] == ["caf\udce9"]
    assert not {"rbytes", "threshold", "units", "watched"} & loaded.keys()


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
        "sets": [{"name": "Café & co", "dirs": 2}, {"name": "Empty", "dirs": 0}],
    }
    assert not {"units", "watched"} & index_entry(TREE, HISTORY, T2).keys()


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


def test_sets_is_required(capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["--db", "x"])
    assert "--sets" in capsys.readouterr().err


@pytest.mark.parametrize("option", ["--threshold", "--max-units", "--watch"])
def test_removed_options_are_gone(option, capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["--sets", "s.toml", option, "1"])
    assert "unrecognized arguments" in capsys.readouterr().err


@pytest.mark.parametrize("value", ["0", "-1", "x"])
def test_max_dirs_must_be_positive(value, capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["--sets", "s.toml", "--max-dirs", value])
    assert "--max-dirs" in capsys.readouterr().err


def _write(tmp_path, text, name="sets.toml"):
    path = tmp_path / name
    path.write_text(text)
    return path


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_unwritable_out_is_an_error_not_a_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: _Conn())
    monkeypatch.setattr(generate.db, "check_schema", lambda conn: None)
    monkeypatch.setattr(generate.db, "trees", lambda conn: [TREE])
    sets = _write(tmp_path, "")
    blocker = tmp_path / "file"
    blocker.write_text("")
    argv = ["--db", "x", "--out", str(blocker / "data"), "--sets", str(sets)]
    assert generate.main(argv) == 1
    assert capsys.readouterr().err.startswith("ERROR: ")


def test_database_errors_are_errors_not_tracebacks(monkeypatch, tmp_path, capsys):
    def denied(conn):
        raise psycopg.errors.InsufficientPrivilege("permission denied for schema")

    monkeypatch.setattr(generate.db, "connect", lambda dsn: _Conn())
    monkeypatch.setattr(generate.db, "check_schema", denied)
    sets = _write(tmp_path, "")
    assert generate.main(["--db", "x", "--sets", str(sets)]) == 1
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
        ["--recent", "30d", "--max-points", "400"],  # 721 hourly points > 400
        ["--max-points", "1"],
    ],
)
def test_resolution_options_are_checked(argv, capsys):
    with pytest.raises(SystemExit):
        generate.main(["--db", "x", "--sets", "s.toml", *argv])
    assert "error:" in capsys.readouterr().err


def test_resolution_options_reach_summarize():
    args = parser().parse_args(
        [
            "--sets",
            "s.toml",
            "--recent",
            "2d",
            "--max-points",
            "100",
        ]
    )
    assert generate.resolution(args) == Resolution(
        recent=timedelta(days=2), max_points=100
    )


@pytest.mark.parametrize(
    "d", [timedelta(0), timedelta(days=7), timedelta(hours=36), timedelta(seconds=90)]
)
def test_format_duration_round_trips(d):
    assert parse_duration(format_duration(d)) == d


def _set(name, *patterns):
    return DirSet(name, [DirGroup([parse_pattern(t) for t in patterns], 0)])


def test_set_warnings():
    sets = [_set("Top", "x", "*", "."), _set("Deep", "y")]
    found = [SetDirs("Top", [b"a"], [0, 3, 0]), SetDirs("Deep", [], [0])]
    head = "WARNING: tree 2 (/npx/ana): set"
    assert set_warnings(TREE, sets, found) == [
        f"{head} 'Top': pattern 'x' matches no directory",
        f"{head} 'Top': pattern '.' matches no directory",
        f"{head} 'Deep': pattern 'y' matches no directory",
    ]


def test_set_warnings_skip_the_unreplaced_implicit_set():
    assert set_warnings(TREE, [IMPLICIT], [SetDirs("Top level", [], [0])]) == []
    replaced = _set("Top level", "x")
    assert set_warnings(TREE, [replaced], [SetDirs("Top level", [], [0])]) == [
        "WARNING: tree 2 (/npx/ana): set 'Top level': pattern 'x' matches no directory"
    ]


def _config(**sections):
    """Return a sets.load result: SECTIONS maps a tree path (`_` for `/`) to
    the pattern texts of its one set, "s"."""
    return {k.replace("_", "/").encode(): [_set("s", *v)] for k, v in sections.items()}


def _names(by_tree):
    return {i: [s.name for s in sets] for i, sets in by_tree.items()}


def test_tree_sets_goes_by_tree_root_and_adds_the_implicit_set_everywhere():
    a = Tree(1, "c", None, b"/npx/user", 2)
    b = Tree(2, "c", None, b"/npx/user/vbrik", 2)
    c = Tree(3, "c", None, b"/sim", 2)
    by_tree = tree_sets(_config(_npx_user=["*"], _sim=["x", "y"]), [a, b, c])
    assert _names(by_tree) == {
        1: ["Top level", "s"],
        2: ["Top level"],
        3: ["Top level", "s"],
    }
    assert [p.text for p, _ in by_tree[3][1].patterns()] == ["x", "y"]
    assert by_tree[2] == [IMPLICIT]


def test_tree_sets_rejects_a_section_without_a_tree():
    a = Tree(1, "c", None, b"/exp", 2)
    with pytest.raises(ValueError, match=r"--sets: .*/exq"):
        tree_sets(_config(_exp=["*"], _exq=["*"]), [a])


def test_tree_sets_rejects_a_root_shared_by_trees():
    a = Tree(1, "c", None, b"/exp", 2)
    b = Tree(2, "d", None, b"/exp", 2)
    with pytest.raises(ValueError, match=r"--sets: trees 1, 2.*/exp"):
        tree_sets(_config(_exp=["*"]), [a, b])
    # without a section, sharing a root is harmless
    assert _names(tree_sets({}, [a, b])) == {1: ["Top level"], 2: ["Top level"]}


def test_bad_sets_file_is_an_error_before_the_database(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: pytest.fail("connected"))
    f = _write(tmp_path, 'watched = ["/a"]\n')
    assert generate.main(["--db", "x", "--sets", str(f)]) == 1
    assert capsys.readouterr().err.startswith("ERROR: ")


def test_missing_sets_file_is_an_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: pytest.fail("connected"))
    assert generate.main(["--db", "x", "--sets", str(tmp_path / "nope.toml")]) == 1
    assert "nope.toml" in capsys.readouterr().err


class _TxConn(_Conn):
    def transaction(self):
        return _Conn()


def _fake_db(monkeypatch, trees):
    """Serve TREES ({Tree: [(path, rbytes)] of one sample}) as the database,
    with rentries = rbytes // 2."""
    rows = {t.id: r for t, r in trees.items()}
    monkeypatch.setattr(generate.db, "connect", lambda dsn: _TxConn())
    monkeypatch.setattr(generate.db, "check_schema", lambda conn: None)
    monkeypatch.setattr(generate.db, "trees", lambda conn: list(trees))
    monkeypatch.setattr(generate.db, "samples", lambda conn, tid: [Sample(tid, T1)])
    monkeypatch.setattr(generate.db, "current_errors", lambda conn, tid: 0)
    monkeypatch.setattr(generate.db, "changed_paths", lambda conn, later: set())
    monkeypatch.setattr(
        generate.db, "sample_paths", lambda conn, sid: (p for p, _ in rows[sid])
    )
    monkeypatch.setattr(
        generate.db,
        "path_rows",
        lambda conn, tid, paths: [
            (tid, p, b, b // 2, False) for p, b in rows[tid] if p in paths
        ],
    )


def test_run_writes_sets_split_and_compact_series(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    _fake_db(monkeypatch, {exp: [(ROOT, 9), (b"a", 5), (b"a/x", 3), (b"b", 4)]})
    sets = _write(
        tmp_path, '[tree."/exp".set.B]\ndirs = [{ match = ["a"], depth = 1 }]\n'
    )
    argv = ["--db", "x", "--out", str(tmp_path / "d"), "--sets", str(sets)]
    assert generate.main(argv) == 0
    doc = json.loads((tmp_path / "d" / "tree-1.json").read_text())
    assert doc["sets"] == [
        {"name": "Top level", "paths": ["a", "b"]},
        {"name": "B", "paths": ["a"]},
    ]
    assert doc["split"] == ["a"]
    assert doc["series"] == {"a": [5, 2], "a/x": [3, 1], "b": [4, 2]}
    assert "units" not in doc and "threshold" not in doc
    index = json.loads((tmp_path / "d" / "trees.json").read_text())
    assert index[0]["sets"] == [
        {"name": "Top level", "dirs": 2},
        {"name": "B", "dirs": 1},
    ]
    assert "WARNING" not in capsys.readouterr().err


def test_run_gives_trees_without_a_section_the_implicit_set(monkeypatch, tmp_path):
    exp = Tree(1, "c", None, b"/exp", 2)
    sim = Tree(2, "c", None, b"/sim", 2)
    _fake_db(
        monkeypatch,
        {exp: [(ROOT, 9), (b"a", 5)], sim: [(ROOT, 9), (b"s", 9), (b"t", 0)]},
    )
    sets = _write(
        tmp_path, '[tree."/exp".set.A]\ndirs = [{ match = ["a"], depth = 0 }]\n'
    )
    out = tmp_path / "d"
    assert generate.main(["--db", "x", "--out", str(out), "--sets", str(sets)]) == 0
    doc = json.loads((out / "tree-2.json").read_text())
    assert doc["sets"] == [{"name": "Top level", "paths": ["s", "t"]}]
    assert doc["split"] == []


def test_empty_sets_file_is_valid(monkeypatch, tmp_path):
    exp = Tree(1, "c", None, b"/exp", 2)
    _fake_db(monkeypatch, {exp: [(ROOT, 9), (b"a", 5)]})
    sets = _write(tmp_path, "")
    out = tmp_path / "d"
    assert generate.main(["--db", "x", "--out", str(out), "--sets", str(sets)]) == 0
    sets_of_tree = json.loads((out / "tree-1.json").read_text())["sets"]
    assert sets_of_tree == [{"name": "Top level", "paths": ["a"]}]


def test_run_warns_relative_to_selected_trees(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    sim = Tree(2, "c", None, b"/sim", 2)
    _fake_db(
        monkeypatch,
        {exp: [(ROOT, 9), (b"a", 5), (b"b", 4)], sim: [(ROOT, 9), (b"s", 9)]},
    )
    sets = _write(
        tmp_path,
        '[tree."/exp".set.B]\ndirs = [{ match = ["b"], depth = 0 }]\n'
        '[tree."/sim".set.Gone]\ndirs = [{ match = ["gone"], depth = 0 }]\n',
    )
    argv = ["--db", "x", "--out", str(tmp_path / "d"), "--sets", str(sets)]
    assert generate.main([*argv, "--tree", "1"]) == 0
    assert "WARNING" not in capsys.readouterr().err  # /sim not run

    assert generate.main(argv) == 0
    assert capsys.readouterr().err == (
        "WARNING: tree 2 (/sim): set 'Gone': pattern 'gone' matches no directory\n"
    )


def test_run_keeps_patterns_to_their_own_tree(monkeypatch, tmp_path):
    # /npx/user/vbrik is a tree of its own, and /npx/user's "*" must not
    # make its root a stored dir
    users = Tree(1, "c", None, b"/npx/user", 2)
    vbrik = Tree(2, "c", None, b"/npx/user/vbrik", 2)
    _fake_db(
        monkeypatch,
        {
            users: [(ROOT, 9), (b"vbrik", 5), (b"other", 4)],
            vbrik: [(ROOT, 5), (b"data", 5)],
        },
    )
    sets = _write(
        tmp_path,
        '[tree."/npx/user".set.Users]\ndirs = [{ match = ["*"], depth = 0 }]\n',
    )
    out = tmp_path / "d"
    assert generate.main(["--db", "x", "--out", str(out), "--sets", str(sets)]) == 0
    users_doc = json.loads((out / "tree-1.json").read_text())
    assert users_doc["sets"][1] == {"name": "Users", "paths": ["other", "vbrik"]}
    vbrik_doc = json.loads((out / "tree-2.json").read_text())
    assert vbrik_doc["sets"] == [{"name": "Top level", "paths": ["data"]}]


def test_run_fails_for_a_section_without_a_tree(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    _fake_db(monkeypatch, {exp: [(ROOT, 9)]})
    sets = _write(
        tmp_path, '[tree."/exq".set.s]\ndirs = [{ match = ["*"], depth = 0 }]\n'
    )
    out = tmp_path / "d"
    argv = ["--db", "x", "--out", str(out), "--sets", str(sets), "--tree", "1"]
    assert generate.main(argv) == 1  # even though --tree 1 would not read it
    assert "/exq" in capsys.readouterr().err
    assert not (out / "tree-1.json").exists()


def test_run_fails_when_stored_dirs_exceed_the_cap(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    _fake_db(monkeypatch, {exp: [(ROOT, 9), (b"a", 5), (b"b", 4)]})
    sets = _write(tmp_path, "")
    out = tmp_path / "d"
    argv = ["--db", "x", "--out", str(out), "--sets", str(sets)]
    assert generate.main([*argv, "--max-dirs", "1"]) == 1
    assert capsys.readouterr().err == (
        "ERROR: tree 1 (/exp): its sets store 2 directories, more than --max-dirs 1"
        " (Top level: 2). Lower their depths, narrow their patterns or raise"
        " --max-dirs.\n"
    )
    assert not (out / "tree-1.json").exists()
    assert generate.main([*argv, "--max-dirs", "2"]) == 0


def test_busiest_hint_groups_siblings_under_their_parent():
    hint = busiest_hint(
        {
            1: [(b"a", 1300), (b"b", 20)],
            3: [(b"a/big/x", 400), (b"a/p/1", 300), (b"b/q/r", 250), (b"a/p/2", 200)],
        }
    )
    assert hint == (
        "Directories with the most stored subdirectories, by depth:\n"
        "  depth 1:\n"
        "    1300  a\n"
        "      20  b\n"
        "  depth 3:\n"
        "     400  a/big/x\n"
        "          a/p/\n"
        "     300    1\n"
        "     200    2\n"
        "     250  b/q/r"
    )


def test_busiest_hint_shows_non_utf8_names_escaped():
    assert busiest_hint({2: [(CAFE, 5)]}).endswith("5  caf\\xe9/x")


def test_too_many_dirs_error_names_the_busiest_dirs(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    _fake_db(monkeypatch, {exp: [(ROOT, 9), (b"a", 5), (b"a/x", 3), (b"b", 4)]})
    sets = _write(
        tmp_path, '[tree."/exp".set.B]\ndirs = [{ match = ["*"], depth = 1 }]\n'
    )
    argv = ["--db", "x", "--out", str(tmp_path / "d"), "--sets", str(sets)]
    assert generate.main([*argv, "--max-dirs", "2"]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: tree 1 (/exp): its sets store 3 directories")
    assert err.endswith("raise --max-dirs.\n" + busiest_hint({1: [(b"a", 1)]}) + "\n")
