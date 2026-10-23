import re

import pytest

from dirhistory.watch import Matcher, Watched, load, parse_pattern, select


def test_parse_pattern_splits_components():
    assert parse_pattern("/npx/ana/*/filtered").parts == (
        b"npx",
        b"ana",
        b"*",
        b"filtered",
    )
    assert parse_pattern("/").parts == ()


@pytest.mark.parametrize(
    "text", ["npx/ana", "", "/a//b", "/a/", "/a/./b", "/a/../b", "/a/**/b"]
)
def test_parse_pattern_rejects(text):
    with pytest.raises(ValueError, match=re.escape(repr(text))):
        parse_pattern(text)


def test_for_tree_strips_the_tree_part():
    p = parse_pattern("/npx/*/IceCube/*")
    assert p.for_tree(b"/npx/ana") == (b"IceCube", b"*")
    assert p.for_tree(b"/npx") == (b"*", b"IceCube", b"*")
    assert p.for_tree(b"/sim") is None
    assert parse_pattern("/npx/ana").for_tree(b"/npx/ana") == ()
    assert parse_pattern("/npx").for_tree(b"/npx/ana") is None  # shorter than the tree
    assert parse_pattern("/a").for_tree(b"/") == (b"a",)


def test_matcher_matches_per_component():
    m = Matcher([(b"*",), (b"a", b"*"), None, ()])
    for path in [b".", b"a", b"a/b", b"a/b/c", b"z"]:
        m.add(path)
    assert m.matches() == [[b"a", b"z"], [b"a/b"], [], [b"."]]  # "*" never crosses "/"


def test_matcher_matches_names_that_are_not_utf8():
    m = Matcher([(b"caf*",)])
    m.add(b"caf\xe9")
    assert m.matches() == [[b"caf\xe9"]]


def test_matcher_ignores_repeated_adds():
    m = Matcher([(b"*",)])
    m.add(b"a")
    m.add(b"a")
    assert m.matches() == [[b"a"]]


def test_select_keeps_config_order_and_first_occurrence():
    w = select([[b"b", b"c"], [b"a", b"b"]], limit=10)
    assert w == Watched([b"b", b"c", b"a"], [2, 2], 0)


def test_select_caps_and_counts_dropped():
    assert select([[b"a", b"b", b"c"]], limit=2) == Watched([b"a", b"b"], [3], 1)


def test_load_reads_patterns(tmp_path):
    f = tmp_path / "watch.toml"
    f.write_text('watch = ["/a/*", "/b"]\n')
    assert [p.text for p in load(f)] == ["/a/*", "/b"]


def test_load_accepts_an_empty_list(tmp_path):
    f = tmp_path / "watch.toml"
    f.write_text("watch = []\n")
    assert load(f) == []


@pytest.mark.parametrize(
    "content",
    [
        'watched = ["/a"]\n',  # unknown key
        'watch = "/a"\n',  # not a list
        "watch = [1]\n",  # not strings
        "watch = [\n",  # TOML syntax
        "",  # no watch key
        'watch = ["a"]\n',  # relative pattern
    ],
)
def test_load_rejects(tmp_path, content):
    f = tmp_path / "watch.toml"
    f.write_text(content)
    with pytest.raises(ValueError, match=re.escape(str(f))):
        load(f)


def test_load_missing_file_is_an_oserror(tmp_path):
    with pytest.raises(OSError):
        load(tmp_path / "nope.toml")
