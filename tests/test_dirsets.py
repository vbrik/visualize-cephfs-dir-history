import re

import pytest

from dirhistory.dirsets import (
    IMPLICIT,
    Cut,
    DirGroup,
    DirSet,
    DirSetDirs,
    Matcher,
    TreeConfig,
    cut,
    level,
    load,
    parse_pattern,
    select,
    with_implicit,
)


def _dirset(name, *pairs):
    """A DirSet with one group per (pattern text, depth) in PAIRS."""
    return DirSet(name, [DirGroup([parse_pattern(t)], d) for t, d in pairs])


def _toml(tmp_path, text, budget=1):
    """Write TEXT to a dirsets file, after a top-level `budget = BUDGET` unless
    BUDGET is None, and return its path."""
    f = tmp_path / "dirsets.toml"
    f.write_text(text if budget is None else f"budget = {budget}\n{text}")
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
    m = Matcher([_dirset("s", ("*", 0), ("a/*", 0), (".", 0))])
    for path in [b".", b"a", b"a/b", b"a/b/c", b"z"]:
        m.add(path)
    assert m.matches() == [[[b"a", b"z"], [b"a/b"], [b"."]]]  # "*" never crosses "/"


def test_matcher_matches_names_that_are_not_utf8():
    m = Matcher([_dirset("s", ("caf*", 1))])
    m.add(b"caf\xe9")
    m.add(b"caf\xe9/x")
    assert m.matches() == [[[b"caf\xe9"]]]
    assert m.stored() == {b"caf\xe9": 1, b"caf\xe9/x": 0}


def test_matcher_wants_paths_only_with_patterns():
    # summarize skips offering millions of paths to a matcher that wants none
    assert not Matcher([]).wants
    assert not Matcher([DirSet("s", [])]).wants
    assert Matcher([_dirset("s", ("a", 0))]).wants


def test_matcher_stores_descendants_within_depth():
    m = Matcher([_dirset("s", ("a", 2))])
    for p in [b"a", b"a/x", b"a/x/y", b"a/x/y/z", b"b", b"b/x"]:
        m.add(p)
    assert m.stored() == {b"a": 2, b"a/x": 1, b"a/x/y": 0}
    assert m.matches() == [[[b"a"]]]


def test_matcher_checks_the_pattern_prefix_of_descendants():
    m = Matcher([_dirset("s", ("a/*", 1))])
    for p in [b"a/b", b"a/b/c", b"x/b/c", b"x/y"]:
        m.add(p)
    assert m.stored() == {b"a/b": 1, b"a/b/c": 0}


def test_matcher_largest_depth_wins_across_groups_and_dirsets():
    m = Matcher([_dirset("s", ("a", 0)), _dirset("t", ("*", 1))])
    for p in [b"a", b"a/x"]:
        m.add(p)
    assert m.stored() == {b"a": 1, b"a/x": 0}


def test_matcher_largest_depth_wins_within_a_dirset():
    m = Matcher([_dirset("s", ("a", 0), ("a", 2))])
    for p in [b"a", b"a/x", b"a/x/y"]:
        m.add(p)
    assert m.stored() == {b"a": 2, b"a/x": 1, b"a/x/y": 0}


def test_matcher_root_pattern_covers_children():
    m = Matcher([_dirset("s", (".", 1))])
    for p in [b".", b"a", b"a/b"]:
        m.add(p)
    assert m.stored() == {b".": 1, b"a": 0}


def test_matcher_ignores_repeated_adds():
    m = Matcher([_dirset("s", ("*", 1))])
    for p in [b"a", b"a", b"a/x", b"a/x"]:
        m.add(p)
    assert m.matches() == [[[b"a"]]]
    assert m.stored() == {b"a": 1, b"a/x": 0}


def test_select_keeps_config_order_and_first_occurrence():
    w = select("s", [[b"b", b"c"], [b"a", b"b"]], {b"a", b"b", b"c"})
    assert w == DirSetDirs("s", [b"b", b"c", b"a"], [2, 2], 0)


def test_select_leaves_out_cut_paths():
    w = select("s", [[b"b", b"c"], [b"a", b"b"]], {b"b"})
    assert (w.paths, w.found, w.dropped) == ([b"b"], [2, 2], 2)


def test_with_implicit_comes_first():
    s = DirSet("s", [])
    assert with_implicit([s]) == [IMPLICIT, s]
    assert with_implicit([]) == [IMPLICIT]


def test_with_implicit_is_replaced_by_a_dirset_of_its_name():
    mine = DirSet("s", [])
    top = DirSet("Top level", [DirGroup([parse_pattern("*")], 2)])
    assert with_implicit([mine, top]) == [top, mine]


def test_implicit_is_the_top_level_dirs_only():
    assert IMPLICIT.name == "Top level"
    assert [(p.text, d) for p, d in IMPLICIT.patterns()] == [("*", 0)]


def _texts(config):
    """CONFIG (a load result) as {root: {dirset name: [(pattern text, depth)]}}."""
    return {
        root: {s.name: [(p.text, d) for p, d in s.patterns()] for s in tree.dirsets}
        for root, tree in config.items()
    }


def test_load_reads_groups_with_depths(tmp_path):
    f = _toml(
        tmp_path,
        '[tree."/exp".dirset."s"]\ndirs = [\n'
        '  { match = ["a", "b"], depth = 0 },\n  { match = ["x*"], depth = 3 },\n]\n',
    )
    (s,) = load(f)[b"/exp"].dirsets
    assert s.name == "s"
    assert [(p.text, d) for p, d in s.patterns()] == [("a", 0), ("b", 0), ("x*", 3)]
    assert [g.depth for g in s.groups] == [0, 3]


def test_load_reads_the_dirsets_of_each_tree(tmp_path):
    f = _toml(
        tmp_path,
        '[tree."/npx/user".dirset."Users"]\ndirs = [{ match = ["*"], depth = 1 }]\n'
        '[tree."/npx/user".dirset."Subdirs"]\ndirs = [{ match = ["*/*"], depth = 0 }]\n'
        '[tree."/npx/user/vbrik".dirset.x]\n'
        'dirs = [{ match = ["IceCube/*/filtered", "."], depth = 2 }]\n',
    )
    assert _texts(load(f)) == {
        b"/npx/user": {"Users": [("*", 1)], "Subdirs": [("*/*", 0)]},
        b"/npx/user/vbrik": {"x": [("IceCube/*/filtered", 2), (".", 2)]},
    }


def test_load_does_not_add_the_implicit_dirset(tmp_path):
    f = _toml(tmp_path, '[tree."/a".dirset]\n')
    assert load(f) == {b"/a": TreeConfig(1, [])}


def test_load_keeps_the_dirsets_in_file_order(tmp_path):
    f = _toml(
        tmp_path,
        "".join(
            f'[tree."/a".dirset.{n}]\ndirs = [{{ match = ["a"], depth = 0 }}]\n'
            for n in "bca"
        ),
    )
    assert [s.name for s in load(f)[b"/a"].dirsets] == ["b", "c", "a"]


T = '[tree."/a".dirset.s]\ndirs = [{ match = ["*"], depth = 0 }]\n'
B = '[tree."/b".dirset.s]\ndirs = [{ match = ["*"], depth = 0 }]\n'


def test_load_reads_a_top_level_budget(tmp_path):
    assert load(_toml(tmp_path, T, budget=5))[b"/a"].budget == 5


def test_load_tree_budget_overrides_the_top_level_one(tmp_path):
    f = _toml(tmp_path, '[tree."/a"]\nbudget = 7\n' + T + B, budget=5)
    assert {root: tree.budget for root, tree in load(f).items()} == {
        b"/a": 7,
        b"/b": 5,
    }


def test_load_reads_a_tree_budget_without_a_top_level_one(tmp_path):
    f = _toml(tmp_path, '[tree."/a"]\nbudget = 7\n' + T, budget=None)
    assert load(f)[b"/a"].budget == 7


def test_load_rejects_missing_budget_and_names_both_places(tmp_path):
    f = _toml(tmp_path, '[tree."/a"]\nbudget = 7\n' + T + B, budget=None)
    with pytest.raises(ValueError) as e:
        load(f)
    message = str(e.value)
    assert str(f) in message and "'/b'" in message and "'/a'" not in message
    assert '[tree."/b"]' in message and "top of the file" in message


@pytest.mark.parametrize("value", ["0", "-1", "true", "1.5", '"10"'])
@pytest.mark.parametrize("where", ["top", "tree"])
def test_load_rejects_bad_budgets(tmp_path, value, where):
    if where == "top":
        f = _toml(tmp_path, T, budget=value)
    else:
        f = _toml(tmp_path, f'[tree."/a"]\nbudget = {value}\n' + T)
    with pytest.raises(ValueError, match="'budget' must be an integer >= 1") as e:
        load(f)
    assert str(f) in str(e.value)
    if where == "tree":
        assert "tree '/a'" in str(e.value)


@pytest.mark.parametrize("content", ["", "[tree]\n"])
@pytest.mark.parametrize("budget", [None, 5])
def test_load_rejects_a_file_without_trees(tmp_path, content, budget):
    f = _toml(tmp_path, content, budget=budget)
    with pytest.raises(ValueError, match="no trees") as e:
        load(f)
    assert str(f) in str(e.value)


def test_load_rejects_a_tree_with_only_a_budget(tmp_path):
    f = _toml(tmp_path, '[tree."/a"]\nbudget = 3\n', budget=5)
    with pytest.raises(ValueError, match="'dirset' must be a table of dirsets"):
        load(f)


def test_load_rejects_unknown_keys_next_to_budget(tmp_path):
    with pytest.raises(ValueError, match="unknown key 'budgets'"):
        load(_toml(tmp_path, "budgets = 1\n" + T))
    with pytest.raises(ValueError, match="unknown key 'budgets'"):
        load(_toml(tmp_path, '[tree."/a"]\nbudgets = 1\n' + T))


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
        'dirs = [{ match = ["a"], depth = 0 }]\nextra = 1',  # unknown key in a dirset
    ],
)
def test_load_rejects_bad_groups(tmp_path, content):
    f = _toml(tmp_path, f'[tree."/exp".dirset."s"]\n{content}\n')
    with pytest.raises(ValueError, match=r"tree '/exp', dirset 's'") as e:
        load(f)
    assert str(f) in str(e.value)


def test_load_points_watch_to_dirs(tmp_path):
    f = _toml(tmp_path, '[tree."/exp".dirset."s"]\nwatch = ["a"]\n')
    with pytest.raises(ValueError, match=re.escape("dirs = [{ match = [")):
        load(f)


@pytest.mark.parametrize(
    "content",
    [
        'trees = ["/a"]\n',  # unknown top-level key
        "tree = 1\n",  # not a table
        "[tree]\na = 1\n",  # section is not a table
        '[tree."/a"]\n',  # no dirset
        '[tree."/a".dirset.s]\ndirs = [{ match = ["a"], depth = 0 }]\n'
        '[tree."/a"]\nx=1\n',
        '[tree."/a"]\ndirset = 1\n',  # dirsets not a table
        '[tree."/a"]\ndirset = {s = 1}\n',  # dirset not a table
        '[tree."/a".dirset.""]\ndirs = [{ match = ["a"], depth = 0 }]\n',  # empty name
        '[tree."/a".dirset." "]\ndirs = [{ match = ["a"], depth = 0 }]\n',  # blank name
        '[tree."/a".dirset.s]\ndirs = [\n',  # TOML syntax
        '[tree."a".dirset.s]\n',  # relative tree
        '[tree."/a/".dirset.s]\n',  # trailing slash
        '[tree."/a/../b".dirset.s]\n',  # .. component
        '[tree."".dirset.s]\n',  # empty tree
    ],
)
def test_load_rejects(tmp_path, content):
    f = _toml(tmp_path, content)
    with pytest.raises(ValueError, match=re.escape(str(f))):
        load(f)


@pytest.mark.parametrize(
    "content",
    [
        'watch = ["/a/*"]\n',
        '[tree."/a"]\nwatch = ["*"]\n',
        '[tree."/a".set.s]\ndirs = [{ match = ["a"], depth = 0 }]\n',  # pre-dirset
    ],
)
def test_load_points_old_formats_to_the_new_one(tmp_path, content):
    f = _toml(tmp_path, content)
    with pytest.raises(ValueError, match=r'\[tree\."/.*"\.dirset\."NAME"\]'):
        load(f)


def test_load_names_the_dirset_of_a_bad_pattern(tmp_path):
    f = _toml(
        tmp_path, '[tree."/a".dirset."Users"]\ndirs = [{ match = ["/x"], depth = 0 }]\n'
    )
    with pytest.raises(ValueError, match=r"tree '/a', dirset 'Users'"):
        load(f)


def test_load_missing_file_is_an_oserror(tmp_path):
    with pytest.raises(OSError):
        load(tmp_path / "nope.toml")


def _stored(**dirs):
    """A Matcher.stored() result: DIRS maps a path (`__` for `/`, `_` for
    the root `.`) to its depth left."""
    return {
        (b"." if k == "_" else k.replace("__", "/").encode()): d
        for k, d in dirs.items()
    }


def test_level():
    assert level(b".") == 0
    assert level(b"a") == 1
    assert level(b"a/b") == 2


def test_cut_keeps_everything_within_budget():
    stored = _stored(a=1, a__x=0, b=0)
    assert cut(stored, 4) == Cut(stored, [], None)


def test_cut_exact_budget_is_no_cut():
    stored = _stored(a=1, a__x=0, b=0)
    assert cut(stored, 3) == Cut(stored, [], None)
    boundary = _stored(a=1, b=1, a__x=0, b__y=0)
    assert cut(boundary, 4) == Cut(boundary, [], None)


def test_cut_stops_between_levels():
    c = cut(_stored(a=1, b=1, a__x=0, a__y=0, b__z=0), 2)
    assert c == Cut({b"a": 1, b"b": 1}, [b"a", b"b"], 2)


def test_cut_fills_a_level_by_whole_parents():
    # a's 1 fits (1 left), b's 2 do not: filling stops, c/w (which fits) too
    c = cut(_stored(a=1, b=1, c=1, a__x=0, b__y=0, b__z=0, c__w=0), 5)
    assert sorted(c.kept) == [b"a", b"a/x", b"b", b"c"]
    assert (c.pruned, c.level) == ([b"b", b"c"], 2)


def test_cut_first_unit_too_big_keeps_none_of_the_level():
    c = cut(_stored(a=1, a__x=0, a__y=0), 2)
    assert c == Cut({b"a": 1}, [b"a"], 2)


def test_cut_fills_unparented_dirset_dirs_one_by_one():
    c = cut(_stored(a=0, b=0, c=0), 2)
    assert c == Cut({b"a": 0, b"b": 0}, [], 1)


def test_cut_does_not_prune_a_dir_stored_at_depth_0():
    # q is stored for itself only; q/r is a dirset dir of its own
    c = cut(_stored(q=0, q__r=0, a=0), 2)
    assert c == Cut({b"a": 0, b"q": 0}, [], 2)


def test_cut_never_prunes_dirs_without_candidate_subdirs():
    # a/x has depth left, but the tree's max_depth stops it
    stored = _stored(a=2, a__x=1)
    assert cut(stored, 2) == Cut(stored, [], None)
    assert cut(stored, 1) == Cut({b"a": 2}, [b"a"], 2)


def test_cut_root_is_level_0():
    c = cut(_stored(_=1, a=0, b=0), 2)
    assert c == Cut({b".": 1}, [b"."], 1)


def test_cut_orders_by_bytes():
    stored = {b"caf\xe9": 0, b"cafe": 0, b"Z": 0}
    assert sorted(cut(stored, 2).kept) == [b"Z", b"cafe"]
