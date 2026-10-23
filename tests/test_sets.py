import re

import pytest

from dirhistory.sets import (
    IMPLICIT,
    DirGroup,
    DirSet,
    Matcher,
    SetDirs,
    TooManyDirs,
    load,
    parse_pattern,
    select,
    with_implicit,
)


def _set(name, *pairs):
    """A DirSet with one group per (pattern text, depth) in PAIRS."""
    return DirSet(name, [DirGroup([parse_pattern(t)], d) for t, d in pairs])


def _toml(tmp_path, text):
    """Write TEXT to a sets file and return its path."""
    f = tmp_path / "sets.toml"
    f.write_text(text)
    return f


def test_parse_pattern_splits_components():
    assert parse_pattern("*/filtered").parts == (b"*", b"filtered")
    assert parse_pattern("IceCube/*/filtered").parts == (b"IceCube", b"*", b"filtered")


def test_parse_pattern_dot_is_the_tree_root():
    assert parse_pattern(".").parts == ()


@pytest.mark.parametrize(
    "text",
    ["/npx/ana", "/", "", "a//b", "a/", "a/./b", "./a", "a/../b", "a/**/b", "**"],
)
def test_parse_pattern_rejects(text):
    with pytest.raises(ValueError, match=re.escape(repr(text))):
        parse_pattern(text)


def test_matcher_matches_per_component():
    m = Matcher([_set("s", ("*", 0), ("a/*", 0), (".", 0))])
    for path in [b".", b"a", b"a/b", b"a/b/c", b"z"]:
        m.add(path)
    assert m.matches() == [[[b"a", b"z"], [b"a/b"], [b"."]]]  # "*" never crosses "/"


def test_matcher_matches_names_that_are_not_utf8():
    m = Matcher([_set("s", ("caf*", 1))])
    m.add(b"caf\xe9")
    m.add(b"caf\xe9/x")
    assert m.matches() == [[[b"caf\xe9"]]]
    assert m.stored() == {b"caf\xe9": 1, b"caf\xe9/x": 0}


def test_matcher_wants_paths_only_with_patterns():
    # summarize skips offering millions of paths to a matcher that wants none
    assert not Matcher([]).wants
    assert not Matcher([DirSet("s", [])]).wants
    assert Matcher([_set("s", ("a", 0))]).wants


def test_matcher_stores_descendants_within_depth():
    m = Matcher([_set("s", ("a", 2))])
    for p in [b"a", b"a/x", b"a/x/y", b"a/x/y/z", b"b", b"b/x"]:
        m.add(p)
    assert m.stored() == {b"a": 2, b"a/x": 1, b"a/x/y": 0}
    assert m.matches() == [[[b"a"]]]


def test_matcher_checks_the_pattern_prefix_of_descendants():
    m = Matcher([_set("s", ("a/*", 1))])
    for p in [b"a/b", b"a/b/c", b"x/b/c", b"x/y"]:
        m.add(p)
    assert m.stored() == {b"a/b": 1, b"a/b/c": 0}


def test_matcher_largest_depth_wins_across_groups_and_sets():
    m = Matcher([_set("s", ("a", 0)), _set("t", ("*", 1))])
    for p in [b"a", b"a/x"]:
        m.add(p)
    assert m.stored() == {b"a": 1, b"a/x": 0}
    assert m.counts() == [1, 2]


def test_matcher_largest_depth_wins_within_a_set():
    m = Matcher([_set("s", ("a", 0), ("a", 2))])
    for p in [b"a", b"a/x", b"a/x/y"]:
        m.add(p)
    assert m.stored() == {b"a": 2, b"a/x": 1, b"a/x/y": 0}
    assert m.counts() == [3]


def test_matcher_root_pattern_covers_children():
    m = Matcher([_set("s", (".", 1))])
    for p in [b".", b"a", b"a/b"]:
        m.add(p)
    assert m.stored() == {b".": 1, b"a": 0}


def test_matcher_ignores_repeated_adds():
    m = Matcher([_set("s", ("*", 1))])
    for p in [b"a", b"a", b"a/x", b"a/x"]:
        m.add(p)
    assert m.matches() == [[[b"a"]]]
    assert m.stored() == {b"a": 1, b"a/x": 0}
    assert m.counts() == [2]


def test_matcher_counts_a_path_once_per_set():
    m = Matcher([_set("s", ("a", 1), ("*", 0)), _set("t", ("a/*", 0))])
    for p in [b"a", b"a/x", b"b"]:
        m.add(p)
    assert m.counts() == [3, 1]  # a, a/x, b; a/x


def test_select_keeps_config_order_and_first_occurrence():
    w = select("s", [[b"b", b"c"], [b"a", b"b"]])
    assert w == SetDirs("s", [b"b", b"c", b"a"], [2, 2])


def test_too_many_dirs_names_the_sets_and_counts():
    e = TooManyDirs(30, 20, [("Top level", 10), ("Users", 25)])
    assert (e.found, e.limit, e.counts) == (30, 20, [("Top level", 10), ("Users", 25)])
    assert "30" in str(e) and "20" in str(e)
    assert "'Users' 25" in str(e)


def test_with_implicit_comes_first():
    s = DirSet("s", [])
    assert with_implicit([s]) == [IMPLICIT, s]
    assert with_implicit([]) == [IMPLICIT]


def test_with_implicit_is_replaced_by_a_set_of_its_name():
    mine = DirSet("s", [])
    top = DirSet("Top level", [DirGroup([parse_pattern("*")], 2)])
    assert with_implicit([mine, top]) == [top, mine]


def test_implicit_is_the_top_level_dirs_only():
    assert IMPLICIT.name == "Top level"
    assert [(p.text, d) for p, d in IMPLICIT.patterns()] == [("*", 0)]


def _texts(config):
    """CONFIG (a load result) as {root: {set name: [(pattern text, depth)]}}."""
    return {
        root: {s.name: [(p.text, d) for p, d in s.patterns()] for s in sets}
        for root, sets in config.items()
    }


def test_load_reads_groups_with_depths(tmp_path):
    f = _toml(
        tmp_path,
        '[tree."/exp".set."s"]\ndirs = [\n'
        '  { match = ["a", "b"], depth = 0 },\n  { match = ["x*"], depth = 3 },\n]\n',
    )
    (s,) = load(f)[b"/exp"]
    assert s.name == "s"
    assert [(p.text, d) for p, d in s.patterns()] == [("a", 0), ("b", 0), ("x*", 3)]
    assert [g.depth for g in s.groups] == [0, 3]


def test_load_reads_the_sets_of_each_tree(tmp_path):
    f = _toml(
        tmp_path,
        '[tree."/npx/user".set."Users"]\ndirs = [{ match = ["*"], depth = 1 }]\n'
        '[tree."/npx/user".set."Subdirs"]\ndirs = [{ match = ["*/*"], depth = 0 }]\n'
        '[tree."/npx/user/vbrik".set.x]\n'
        'dirs = [{ match = ["IceCube/*/filtered", "."], depth = 2 }]\n',
    )
    assert _texts(load(f)) == {
        b"/npx/user": {"Users": [("*", 1)], "Subdirs": [("*/*", 0)]},
        b"/npx/user/vbrik": {"x": [("IceCube/*/filtered", 2), (".", 2)]},
    }


def test_load_does_not_add_the_implicit_set(tmp_path):
    f = _toml(tmp_path, '[tree."/a".set]\n')
    assert load(f) == {b"/a": []}


def test_load_keeps_the_sets_in_file_order(tmp_path):
    f = _toml(
        tmp_path,
        "".join(
            f'[tree."/a".set.{n}]\ndirs = [{{ match = ["a"], depth = 0 }}]\n'
            for n in "bca"
        ),
    )
    assert [s.name for s in load(f)[b"/a"]] == ["b", "c", "a"]


def test_load_accepts_a_file_without_trees(tmp_path):
    assert load(_toml(tmp_path, "")) == {}


@pytest.mark.parametrize(
    "content",
    [
        "dirs = []",  # empty dirs
        'dirs = [{ match = ["a"] }]',  # depth missing
        'dirs = [{ match = ["a"], depth = -1 }]',
        'dirs = [{ match = ["a"], depth = 1.5 }]',
        'dirs = [{ match = ["a"], depth = true }]',  # bool is not an int here
        'dirs = [{ match = ["a"], depth = "1" }]',
        "dirs = [{ match = [], depth = 0 }]",
        'dirs = [{ match = "a", depth = 0 }]',  # not a list
        "dirs = [{ match = [1], depth = 0 }]",  # not strings
        "dirs = [{ depth = 0 }]",  # match missing
        'dirs = [{ match = ["a"], depth = 0, x = 1 }]',
        'dirs = ["a"]',
        'dirs = "a"',
        'dirs = [{ match = ["/abs"], depth = 0 }]',
        'dirs = [{ match = ["a/**"], depth = 0 }]',
        "",  # dirs missing
        'dirs = [{ match = ["a"], depth = 0 }]\nextra = 1',  # unknown key in a set
    ],
)
def test_load_rejects_bad_groups(tmp_path, content):
    f = _toml(tmp_path, f'[tree."/exp".set."s"]\n{content}\n')
    with pytest.raises(ValueError, match=r"tree '/exp', set 's'") as e:
        load(f)
    assert str(f) in str(e.value)


def test_load_points_watch_to_dirs(tmp_path):
    f = _toml(tmp_path, '[tree."/exp".set."s"]\nwatch = ["a"]\n')
    with pytest.raises(ValueError, match=re.escape("dirs = [{ match = [")):
        load(f)


@pytest.mark.parametrize(
    "content",
    [
        'trees = ["/a"]\n',  # unknown top-level key
        "tree = 1\n",  # not a table
        "[tree]\na = 1\n",  # section is not a table
        '[tree."/a"]\n',  # no set
        '[tree."/a".set.s]\ndirs = [{ match = ["a"], depth = 0 }]\n[tree."/a"]\nx=1\n',
        '[tree."/a"]\nset = 1\n',  # sets not a table
        '[tree."/a"]\nset = {s = 1}\n',  # set not a table
        '[tree."/a".set.""]\ndirs = [{ match = ["a"], depth = 0 }]\n',  # empty name
        '[tree."/a".set." "]\ndirs = [{ match = ["a"], depth = 0 }]\n',  # blank name
        '[tree."/a".set.s]\ndirs = [\n',  # TOML syntax
        '[tree."a".set.s]\n',  # relative tree
        '[tree."/a/".set.s]\n',  # trailing slash
        '[tree."/a/../b".set.s]\n',  # .. component
        '[tree."".set.s]\n',  # empty tree
    ],
)
def test_load_rejects(tmp_path, content):
    f = _toml(tmp_path, content)
    with pytest.raises(ValueError, match=re.escape(str(f))):
        load(f)


@pytest.mark.parametrize(
    "content", ['watch = ["/a/*"]\n', '[tree."/a"]\nwatch = ["*"]\n']
)
def test_load_points_old_formats_to_the_new_one(tmp_path, content):
    f = _toml(tmp_path, content)
    with pytest.raises(ValueError, match=r'\[tree\."/.*"\.set\."NAME"\]'):
        load(f)


def test_load_names_the_set_of_a_bad_pattern(tmp_path):
    f = _toml(
        tmp_path, '[tree."/a".set."Users"]\ndirs = [{ match = ["/x"], depth = 0 }]\n'
    )
    with pytest.raises(ValueError, match=r"tree '/a', set 'Users'"):
        load(f)


def test_load_missing_file_is_an_oserror(tmp_path):
    with pytest.raises(OSError):
        load(tmp_path / "nope.toml")
