import json
import os
import re
import stat
from datetime import UTC, datetime, timedelta

import psycopg
import pytest

from dirhistory import generate
from dirhistory.db import Tree
from dirhistory.dirsets import (
    IMPLICIT,
    DirGroup,
    DirSet,
    DirSetDirs,
    TreeConfig,
    parse_pattern,
)
from dirhistory.generate import (
    compact,
    configured,
    cut_warning,
    dirset_warnings,
    format_duration,
    index_entry,
    merged_index,
    parse_duration,
    parser,
    select_trees,
    tree_document,
    write_json,
)
from dirhistory.history import ROOT, Resolution, Sample, TreeHistory

TREE = Tree(2, "ad1bf53c-a6ca-11ec-b47e-b04f13b8e306", None, b"/npx/ana", 5)
T1 = datetime(2026, 10, 21, 19, 19, 45, 208826, tzinfo=UTC)
T2 = datetime(2026, 10, 21, 20, 0, tzinfo=UTC)
CAFE = b"caf\xe9/x"  # not UTF-8
HISTORY = TreeHistory(
    [T1, T2],
    {ROOT: [100, 200], b"caf\xe9": [50, 50], CAFE: [10, None]},
    {ROOT: [7, 9], b"caf\xe9": [4, 5], CAFE: [3, None]},
    [DirSetDirs("Café & co", [b"caf\xe9", ROOT], [2]), DirSetDirs("Empty", [], [])],
    [b"caf\xe9"],
    [CAFE],
    3,
    None,
)


def test_compact():
    assert compact([5, 5, 5]) == 5
    assert compact([None, None]) is None
    assert compact([5, 6]) == [5, 6]
    assert compact([None, 5]) == [None, 5]


def test_select_trees_defaults_to_configured():
    exp = Tree(1, "x", "default", b"/exp", 5)
    sim = Tree(3, "x", None, b"/sim", 5)
    assert select_trees([exp, TREE, sim], {1, 3}, []) == [exp, sim]


def test_select_trees_names_unknown_ids():
    with pytest.raises(ValueError, match=r"\b7\b.*\b9\b"):
        select_trees([TREE], {2}, [9, 2, 7])


def test_select_trees_rejects_an_unconfigured_tree():
    exp = Tree(1, "x", "default", b"/exp", 5)
    with pytest.raises(
        ValueError, match=r"tree 2 \(/npx/ana\) has no section in --dirsets"
    ):
        select_trees([exp, TREE], {1}, [2])


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
    assert loaded["dirsets"] == [
        {"name": "Café & co", "paths": ["caf\udce9", "."]},
        {"name": "Empty", "paths": []},
    ]
    assert loaded["split"] == ["caf\udce9"]
    assert loaded["pruned"] == ["caf\udce9/x"]
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
        "dirsets": [{"name": "Café & co", "dirs": 2}, {"name": "Empty", "dirs": 0}],
    }
    assert not {"units", "watched"} & index_entry(TREE, HISTORY, T2).keys()


def test_merged_index_keeps_only_live_configured_trees(tmp_path):
    # tree 3 is gone from the database or from the dirsets file
    old = [{"id": 1, "v": "old"}, {"id": 2, "v": "old"}, {"id": 3, "v": "old"}]
    write_json(tmp_path / "trees.json", old)
    merged = merged_index(tmp_path, [{"id": 2, "v": "new"}], {1, 2})
    assert merged == [{"id": 1, "v": "old"}, {"id": 2, "v": "new"}]


def test_merged_index_without_existing_index(tmp_path):
    assert merged_index(tmp_path, [{"id": 5}], {5}) == [{"id": 5}]


def test_dirsets_is_required(capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["--db", "x"])
    assert "--dirsets" in capsys.readouterr().err


@pytest.mark.parametrize(
    "option", ["--threshold", "--max-units", "--watch", "--max-dirs"]
)
def test_removed_options_are_gone(option, capsys):
    with pytest.raises(SystemExit):
        parser().parse_args(["--dirsets", "s.toml", option, "1"])
    assert "unrecognized arguments" in capsys.readouterr().err


def _write(tmp_path, text, name="dirsets.toml", budget=100):
    """Write TEXT to a dirsets file NAME, after a top-level `budget = BUDGET`
    unless BUDGET is None, and return its path."""
    path = tmp_path / name
    path.write_text(text if budget is None else f"budget = {budget}\n{text}")
    return path


ONE_TREE = '[tree."/npx/ana".dirset.s]\ndirs = [{ match = ["*"], depth = 0 }]\n'


class _Conn:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_unwritable_out_is_an_error_not_a_traceback(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: _Conn())
    monkeypatch.setattr(generate.db, "check_schema", lambda conn: None)
    monkeypatch.setattr(generate.db, "trees", lambda conn: [TREE])
    dirsets = _write(tmp_path, ONE_TREE)
    blocker = tmp_path / "file"
    blocker.write_text("")
    argv = ["--db", "x", "--out", str(blocker / "data"), "--dirsets", str(dirsets)]
    assert generate.main(argv) == 1
    assert capsys.readouterr().err.startswith("ERROR: ")


def test_database_errors_are_errors_not_tracebacks(monkeypatch, tmp_path, capsys):
    def denied(conn):
        raise psycopg.errors.InsufficientPrivilege("permission denied for schema")

    monkeypatch.setattr(generate.db, "connect", lambda dsn: _Conn())
    monkeypatch.setattr(generate.db, "check_schema", denied)
    dirsets = _write(tmp_path, ONE_TREE)
    assert generate.main(["--db", "x", "--dirsets", str(dirsets)]) == 1
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
        generate.main(["--db", "x", "--dirsets", "s.toml", *argv])
    assert "error:" in capsys.readouterr().err


def test_resolution_options_reach_summarize():
    args = parser().parse_args(
        [
            "--dirsets",
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


def _dirset(name, *patterns):
    return DirSet(name, [DirGroup([parse_pattern(t) for t in patterns], 0)])


def test_dirset_warnings():
    dirsets = [_dirset("Top", "x", "*", "."), _dirset("Deep", "y")]
    found = [DirSetDirs("Top", [b"a"], [0, 3, 0]), DirSetDirs("Deep", [], [0])]
    head = "WARNING: tree 2 (/npx/ana): dirset"
    assert dirset_warnings(TREE, dirsets, found) == [
        f"{head} 'Top': pattern 'x' matches no directory",
        f"{head} 'Top': pattern '.' matches no directory",
        f"{head} 'Deep': pattern 'y' matches no directory",
    ]


def test_dirset_warnings_skip_the_unreplaced_implicit_dirset():
    assert dirset_warnings(TREE, [IMPLICIT], [DirSetDirs("Top level", [], [0])]) == []
    replaced = _dirset("Top level", "x")
    assert dirset_warnings(TREE, [replaced], [DirSetDirs("Top level", [], [0])]) == [
        "WARNING: tree 2 (/npx/ana): dirset 'Top level': pattern 'x' matches"
        " no directory"
    ]


def _config(budget=10, **sections):
    """Return a dirsets.load result: SECTIONS maps a tree path (`_` for `/`) to
    the pattern texts of its one dirset, "s"; every tree has BUDGET."""
    return {
        k.replace("_", "/").encode(): TreeConfig(budget, [_dirset("s", *v)])
        for k, v in sections.items()
    }


def _names(by_tree):
    return {i: [s.name for s in tree.dirsets] for i, tree in by_tree.items()}


def test_configured_goes_by_tree_root_and_adds_the_implicit_dirset():
    a = Tree(1, "c", None, b"/npx/user", 2)
    b = Tree(2, "c", None, b"/npx/user/vbrik", 2)
    c = Tree(3, "c", None, b"/sim", 2)
    by_tree = configured(_config(7, _npx_user=["*"], _sim=["x", "y"]), [a, b, c])
    assert _names(by_tree) == {1: ["Top level", "s"], 3: ["Top level", "s"]}
    assert [p.text for p, _ in by_tree[3].dirsets[1].patterns()] == ["x", "y"]
    assert by_tree[3].dirsets[0] == IMPLICIT
    assert {i: tree.budget for i, tree in by_tree.items()} == {1: 7, 3: 7}


def test_configured_rejects_a_section_without_a_tree():
    a = Tree(1, "c", None, b"/exp", 2)
    with pytest.raises(ValueError, match=r"--dirsets: .*/exq"):
        configured(_config(_exp=["*"], _exq=["*"]), [a])


def test_configured_rejects_a_root_shared_by_trees():
    a = Tree(1, "c", None, b"/exp", 2)
    b = Tree(2, "d", None, b"/exp", 2)
    with pytest.raises(ValueError, match=r"--dirsets: trees 1, 2.*/exp"):
        configured(_config(_exp=["*"]), [a, b])
    # without a section, sharing a root is harmless
    assert configured({}, [a, b]) == {}


def test_bad_dirsets_file_is_an_error_before_the_database(
    tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: pytest.fail("connected"))
    f = _write(tmp_path, 'watched = ["/a"]\n')
    assert generate.main(["--db", "x", "--dirsets", str(f)]) == 1
    assert capsys.readouterr().err.startswith("ERROR: ")


def test_missing_dirsets_file_is_an_error(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: pytest.fail("connected"))
    assert generate.main(["--db", "x", "--dirsets", str(tmp_path / "nope.toml")]) == 1
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


def test_run_writes_dirsets_split_and_compact_series(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    _fake_db(monkeypatch, {exp: [(ROOT, 9), (b"a", 5), (b"a/x", 3), (b"b", 4)]})
    dirsets = _write(
        tmp_path, '[tree."/exp".dirset.B]\ndirs = [{ match = ["a"], depth = 1 }]\n'
    )
    argv = ["--db", "x", "--out", str(tmp_path / "d"), "--dirsets", str(dirsets)]
    assert generate.main(argv) == 0
    doc = json.loads((tmp_path / "d" / "tree-1.json").read_text())
    assert doc["dirsets"] == [
        {"name": "Top level", "paths": ["a", "b"]},
        {"name": "B", "paths": ["a"]},
    ]
    assert doc["split"] == ["a"]
    assert doc["series"] == {"a": [5, 2], "a/x": [3, 1], "b": [4, 2]}
    assert "units" not in doc and "threshold" not in doc
    index = json.loads((tmp_path / "d" / "trees.json").read_text())
    assert index[0]["dirsets"] == [
        {"name": "Top level", "dirs": 2},
        {"name": "B", "dirs": 1},
    ]
    assert "WARNING" not in capsys.readouterr().err


def test_run_skips_trees_without_a_section(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    sim = Tree(2, "c", None, b"/sim", 2)
    _fake_db(
        monkeypatch,
        {exp: [(ROOT, 9), (b"a", 5)], sim: [(ROOT, 9), (b"s", 9), (b"t", 0)]},
    )
    dirsets = _write(
        tmp_path, '[tree."/exp".dirset.A]\ndirs = [{ match = ["a"], depth = 0 }]\n'
    )
    out = tmp_path / "d"
    argv = ["--db", "x", "--out", str(out), "--dirsets", str(dirsets), "-v"]
    assert generate.main(argv) == 0
    assert not (out / "tree-2.json").exists()
    assert [e["id"] for e in json.loads((out / "trees.json").read_text())] == [1]
    assert "1 tree without a section in --dirsets skipped" in capsys.readouterr().err


def test_run_rejects_a_tree_option_naming_an_unconfigured_tree(
    monkeypatch, tmp_path, capsys
):
    exp = Tree(1, "c", None, b"/exp", 2)
    sim = Tree(2, "c", None, b"/sim", 2)
    _fake_db(monkeypatch, {exp: [(ROOT, 9)], sim: [(ROOT, 9)]})
    dirsets = _write(
        tmp_path, '[tree."/exp".dirset.A]\ndirs = [{ match = ["a"], depth = 0 }]\n'
    )
    argv = ["--db", "x", "--out", str(tmp_path / "d"), "--dirsets", str(dirsets)]
    assert generate.main([*argv, "--tree", "2"]) == 1
    assert capsys.readouterr().err == (
        "ERROR: tree 2 (/sim) has no section in --dirsets\n"
    )


def test_empty_dirsets_file_is_an_error(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(generate.db, "connect", lambda dsn: pytest.fail("connected"))
    dirsets = _write(tmp_path, "", budget=None)
    assert generate.main(["--db", "x", "--dirsets", str(dirsets)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("ERROR: ") and "no trees" in err


def test_run_warns_relative_to_selected_trees(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    sim = Tree(2, "c", None, b"/sim", 2)
    _fake_db(
        monkeypatch,
        {exp: [(ROOT, 9), (b"a", 5), (b"b", 4)], sim: [(ROOT, 9), (b"s", 9)]},
    )
    dirsets = _write(
        tmp_path,
        '[tree."/exp".dirset.B]\ndirs = [{ match = ["b"], depth = 0 }]\n'
        '[tree."/sim".dirset.Gone]\ndirs = [{ match = ["gone"], depth = 0 }]\n',
    )
    argv = ["--db", "x", "--out", str(tmp_path / "d"), "--dirsets", str(dirsets)]
    assert generate.main([*argv, "--tree", "1"]) == 0
    assert "WARNING" not in capsys.readouterr().err  # /sim not run

    assert generate.main(argv) == 0
    assert capsys.readouterr().err == (
        "WARNING: tree 2 (/sim): dirset 'Gone': pattern 'gone' matches no directory\n"
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
    dirsets = _write(
        tmp_path,
        '[tree."/npx/user".dirset.Users]\ndirs = [{ match = ["*"], depth = 0 }]\n'
        '[tree."/npx/user/vbrik".dirset]\n',
    )
    out = tmp_path / "d"
    assert (
        generate.main(["--db", "x", "--out", str(out), "--dirsets", str(dirsets)]) == 0
    )
    users_doc = json.loads((out / "tree-1.json").read_text())
    assert users_doc["dirsets"][1] == {"name": "Users", "paths": ["other", "vbrik"]}
    vbrik_doc = json.loads((out / "tree-2.json").read_text())
    assert vbrik_doc["dirsets"] == [{"name": "Top level", "paths": ["data"]}]


def test_run_fails_for_a_section_without_a_tree(monkeypatch, tmp_path, capsys):
    exp = Tree(1, "c", None, b"/exp", 2)
    _fake_db(monkeypatch, {exp: [(ROOT, 9)]})
    dirsets = _write(
        tmp_path, '[tree."/exq".dirset.s]\ndirs = [{ match = ["*"], depth = 0 }]\n'
    )
    out = tmp_path / "d"
    argv = ["--db", "x", "--out", str(out), "--dirsets", str(dirsets), "--tree", "1"]
    assert generate.main(argv) == 1  # even though --tree 1 would not read it
    assert "/exq" in capsys.readouterr().err
    assert not (out / "tree-1.json").exists()


CUT_TREE = Tree(1, "c", None, b"/exp", 2)
CUT_ROWS = [(ROOT, 9), (b"a", 5), (b"a/x", 3), (b"a/y", 1), (b"b", 4)]
CUT_DIRSET = '[tree."/exp".dirset.S]\ndirs = [{ match = ["*"], depth = 1 }]\n'
DEEP_DIRSET = '[tree."/exp".dirset.Deep]\ndirs = [{ match = ["*/*"], depth = 0 }]\n'


def _cut_run(monkeypatch, tmp_path, budget, text=CUT_DIRSET, *extra):
    """Run the generator on CUT_TREE with TEXT's dirsets and BUDGET; return the
    exit status and the tree file (None if not written)."""
    _fake_db(monkeypatch, {CUT_TREE: CUT_ROWS})
    dirsets = _write(tmp_path, text, budget=budget)
    out = tmp_path / "d"
    status = generate.main(
        ["--db", "x", "--out", str(out), "--dirsets", str(dirsets), *extra]
    )
    path = out / "tree-1.json"
    return status, json.loads(path.read_text()) if path.exists() else None


def test_run_cuts_a_tree_to_its_budget_and_warns(monkeypatch, tmp_path, capsys):
    status, doc = _cut_run(monkeypatch, tmp_path, 3)
    assert status == 0
    assert (doc["pruned"], doc["split"], set(doc["series"])) == (["a"], [], {"a", "b"})
    assert capsys.readouterr().err == (
        "WARNING: tree 1 (/exp): budget 3 reached: stored 2 of 4 directories,"
        " every level above depth 2 in full; 1 directory whose subdirectories"
        " were left out is marked * in the page. Raise the tree's budget in the"
        " dirsets file to store more.\n"
    )


def test_run_warns_of_dirset_dirs_below_the_cut(monkeypatch, tmp_path, capsys):
    status, doc = _cut_run(monkeypatch, tmp_path, 3, CUT_DIRSET + DEEP_DIRSET)
    assert status == 0
    assert doc["dirsets"][2] == {"name": "Deep", "paths": []}
    err = capsys.readouterr().err
    assert (
        "WARNING: tree 1 (/exp): dirset 'Deep': 2 of its 2 dirs are below the"
        " budget's cut and left out\n"
    ) in err
    assert "matches no directory" not in err


def test_run_does_not_warn_at_exact_budget(monkeypatch, tmp_path, capsys):
    status, doc = _cut_run(monkeypatch, tmp_path, 4)
    assert status == 0
    assert (doc["pruned"], doc["split"]) == ([], ["a"])
    assert "WARNING" not in capsys.readouterr().err


def test_verbose_reports_pruned(monkeypatch, tmp_path, capsys):
    _cut_run(monkeypatch, tmp_path, 3, CUT_DIRSET, "-v")
    assert re.search(
        r"tree 1 \(/exp\): 1 points, 2 stored dirs in 2 dirsets, 1 pruned, [\d.]+ s\n",
        capsys.readouterr().err,
    )


def test_cut_warning_is_none_without_a_cut():
    assert cut_warning(TREE, HISTORY, 10) is None


def test_tree_help_says_the_default_is_the_dirsets_files_trees():
    tree = next(a for a in parser()._actions if a.dest == "tree")
    assert "every tree in --dirsets" in tree.help
