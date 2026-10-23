import re

import pytest

from dirhistory.watch import (
    Matcher,
    TooManyWatched,
    Watched,
    load,
    parse_pattern,
    select,
)


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
    m = Matcher([(b"*",), (b"a", b"*"), ()])
    for path in [b".", b"a", b"a/b", b"a/b/c", b"z"]:
        m.add(path)
    assert m.matches() == [[b"a", b"z"], [b"a/b"], [b"."]]  # "*" never crosses "/"


def test_matcher_matches_names_that_are_not_utf8():
    m = Matcher([(b"caf*",)])
    m.add(b"caf\xe9")
    assert m.matches() == [[b"caf\xe9"]]


def test_matcher_wants_paths_only_with_patterns():
    # summarize skips offering millions of paths to a matcher that wants none
    assert not Matcher([]).wants
    assert Matcher([(b"a",)]).wants


def test_matcher_ignores_repeated_adds():
    m = Matcher([(b"*",)])
    m.add(b"a")
    m.add(b"a")
    assert m.matches() == [[b"a"]]


def test_select_keeps_config_order_and_first_occurrence():
    w = select("s", [[b"b", b"c"], [b"a", b"b"]], limit=10)
    assert w == Watched("s", [b"b", b"c", b"a"], [2, 2])


def test_select_allows_exactly_the_limit():
    w = select("s", [[b"a", b"b"], [b"b"]], limit=2)
    assert w == Watched("s", [b"a", b"b"], [2, 1])


def test_select_raises_over_the_limit():
    with pytest.raises(TooManyWatched) as e:
        select("Users", [[b"a", b"b", b"c"]], limit=2)
    assert (e.value.name, e.value.found, e.value.limit) == ("Users", 3, 2)


def _texts(config):
    """CONFIG (a load result) as {root: {set name: pattern texts}}."""
    return {
        root: {s.name: [p.text for p in s.patterns] for s in sets}
        for root, sets in config.items()
    }


def test_load_reads_the_sets_of_each_tree(tmp_path):
    f = tmp_path / "watch.toml"
    f.write_text(
        '[tree."/npx/user".set."Users"]\nwatch = ["*"]\n'
        '[tree."/npx/user".set."User subdirs"]\nwatch = ["*/*"]\n'
        '[tree."/npx/user/vbrik".set.x]\nwatch = ["IceCube/*/filtered", "."]\n'
        '[tree."/".set."Nothing yet"]\nwatch = []\n'
    )
    assert _texts(load(f)) == {
        b"/npx/user": {"Users": ["*"], "User subdirs": ["*/*"]},
        b"/npx/user/vbrik": {"x": ["IceCube/*/filtered", "."]},
        b"/": {"Nothing yet": []},
    }


def test_load_keeps_the_sets_in_file_order(tmp_path):
    f = tmp_path / "watch.toml"
    f.write_text("".join(f'[tree."/a".set.{n}]\nwatch = []\n' for n in "bca"))
    assert [s.name for s in load(f)[b"/a"]] == ["b", "c", "a"]


def test_load_accepts_a_tree_without_sets(tmp_path):
    f = tmp_path / "watch.toml"
    f.write_text('[tree."/a".set]\n')
    assert load(f) == {b"/a": []}


def test_load_accepts_a_file_without_trees(tmp_path):
    f = tmp_path / "watch.toml"
    f.write_text("")
    assert load(f) == {}


@pytest.mark.parametrize(
    "content",
    [
        'watch = ["a"]\n',  # old format: top-level watch
        'trees = ["/a"]\n',  # unknown top-level key
        "tree = 1\n",  # not a table
        "[tree]\na = 1\n",  # section is not a table
        '[tree."/a"]\n',  # no set
        '[tree."/a"]\nwatch = ["a"]\n',  # old format: watch directly in the tree
        '[tree."/a".set.s]\nwatch = ["a"]\n[tree."/a"]\nextra = 1\n',  # unknown key
        '[tree."/a"]\nset = 1\n',  # sets not a table
        '[tree."/a"]\nset = {s = 1}\n',  # set not a table
        '[tree."/a".set.s]\n',  # no watch
        '[tree."/a".set.s]\nwatch = ["a"]\nextra = 1\n',  # unknown key in a set
        '[tree."/a".set.""]\nwatch = []\n',  # empty set name
        '[tree."/a".set." "]\nwatch = []\n',  # blank set name
        '[tree."/a".set.s]\nwatch = "a"\n',  # not a list
        '[tree."/a".set.s]\nwatch = [1]\n',  # not strings
        '[tree."/a".set.s]\nwatch = [\n',  # TOML syntax
        '[tree."/a".set.s]\nwatch = ["/a/b"]\n',  # absolute pattern
        '[tree."a".set.s]\nwatch = []\n',  # relative tree
        '[tree."/a/".set.s]\nwatch = []\n',  # trailing slash
        '[tree."/a/../b".set.s]\nwatch = []\n',  # .. component
        '[tree."".set.s]\nwatch = []\n',  # empty tree
    ],
)
def test_load_rejects(tmp_path, content):
    f = tmp_path / "watch.toml"
    f.write_text(content)
    with pytest.raises(ValueError, match=re.escape(str(f))):
        load(f)


@pytest.mark.parametrize(
    "content", ['watch = ["/a/*"]\n', '[tree."/a"]\nwatch = ["*"]\n']
)
def test_load_points_old_formats_to_the_new_one(tmp_path, content):
    f = tmp_path / "watch.toml"
    f.write_text(content)
    with pytest.raises(ValueError, match=r'\[tree\."/.*"\.set\."NAME"\]'):
        load(f)


def test_load_names_the_set_of_a_bad_pattern(tmp_path):
    f = tmp_path / "watch.toml"
    f.write_text('[tree."/a".set."Users"]\nwatch = ["/x"]\n')
    with pytest.raises(ValueError, match=r"tree '/a', set 'Users'"):
        load(f)


def test_load_missing_file_is_an_oserror(tmp_path):
    with pytest.raises(OSError):
        load(tmp_path / "nope.toml")
