"""Watch patterns: directories to chart in the page's Watched view.

A watch file lists, per tree, patterns relative to the tree's root, e.g.
`IceCube/*/filtered` under `[tree."/npx/ana"]`. A pattern may hold shell
wildcards (`*`, `?`, `[...]`) and is matched component by component, so `*`
never crosses `/`, and a match has exactly as many components as the
pattern. `.` is the tree root itself. There is no `**`: trees are sampled to
a max depth anyway, and a recursive pattern could watch thousands of
directories by accident.

Trees are named by their root path, which is assumed to be unique among
trees: two trees with the same path (on different clusters or filesystems)
cannot be told apart.
"""

import os
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

# Default most watched dirs per tree; more is an error. It guards against a
# pattern that matches far more than meant: each watched dir is a series in
# the data file and a card on the page.
MAX_WATCHED = 5000

# Path components, as bytes.
type Parts = tuple[bytes, ...]


def _components(path: bytes) -> Parts:
    """Return the components of an absolute or ROOT-relative PATH."""
    return tuple(c for c in path.split(b"/") if c and c != b".")


@dataclass(frozen=True, slots=True)
class WatchPattern:
    """A watch pattern as written (TEXT) and its components (PARTS)."""

    text: str
    parts: Parts


def _split(text: str, banned: set[str], what: str) -> list[str]:
    """Return the components of the slash-separated TEXT. Raises ValueError
    naming TEXT (as WHAT) for an empty component or one in BANNED."""
    parts = text.split("/")
    if bad := ({""} | banned) & set(parts):
        which = "an empty" if "" in bad else f"a {sorted(bad)[0]!r}"
        raise ValueError(f"{what} {text!r} has {which} component")
    return parts


def parse_pattern(text: str) -> WatchPattern:
    """Return the watch pattern in TEXT: a path relative to the tree root, or
    `.` for the root. Raises ValueError naming TEXT if it is absolute or has
    an empty, `.`, `..` or `**` component."""
    if text.startswith("/"):
        raise ValueError(f"watch pattern {text!r} is not relative to the tree root")
    if text == ".":
        return WatchPattern(text, ())
    parts = _split(text, {".", "..", "**"}, "watch pattern")
    return WatchPattern(text, tuple(os.fsencode(p) for p in parts))


def _tree_root(text: str) -> bytes:
    """Return the tree root path in TEXT. Raises ValueError naming TEXT unless
    it is absolute, with no empty, `.` or `..` component (`/` is the root)."""
    if text == "/":
        return b"/"
    if not text.startswith("/"):
        raise ValueError(f"tree {text!r} is not an absolute path")
    _split(text[1:], {".", ".."}, "tree")
    return os.fsencode(text)


def _patterns(tree: str, section: object) -> list[WatchPattern]:
    """Return the patterns of a watch file's `[tree.TREE]` SECTION."""
    if not isinstance(section, dict):
        raise ValueError(f"tree {tree!r} must be a table")
    if extra := sorted(set(section) - {"watch"}):
        raise ValueError(f"tree {tree!r}: unknown key {extra[0]!r}")
    patterns = section.get("watch")
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ValueError(f"tree {tree!r}: 'watch' must be a list of strings")
    return [parse_pattern(p) for p in patterns]


def load(path: Path) -> dict[bytes, list[WatchPattern]]:
    """Return the patterns per tree root of the TOML file at PATH:
    `[tree."/fs/path"]` sections, each with `watch = ["a/*", ...]`.

    Raises ValueError naming PATH for bad TOML, a top-level key other than
    `tree` (the old top-level `watch` gets its own hint), a section that is
    not as above, or a bad tree path or pattern; OSError if PATH cannot be
    read.
    """
    with path.open("rb") as f:
        try:
            doc = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{path}: {e}") from None
    if "watch" in doc:
        raise ValueError(
            f"{path}: a top-level 'watch' is the old format; put the patterns,"
            ' relative to the tree root, in a [tree."/fs/path"] section'
        )
    if extra := sorted(set(doc) - {"tree"}):
        raise ValueError(f"{path}: unknown key {extra[0]!r}; the only key is 'tree'")
    trees = doc.get("tree", {})
    if not isinstance(trees, dict):
        raise ValueError(f"{path}: 'tree' must be a table of trees")
    try:
        return {_tree_root(t): _patterns(t, s) for t, s in trees.items()}
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None


class Matcher:
    """Collects, per pattern, the paths that match it.

    PATTERNS are a tree's patterns' components (WatchPattern.parts). Paths
    are relative to the tree's ROOT (`.` has no components).
    """

    def __init__(self, patterns: Sequence[Parts]):
        self._patterns = patterns
        self._lengths = {len(p) for p in patterns}
        self._found: list[set[bytes]] = [set() for _ in patterns]

    @property
    def wants(self) -> bool:
        """Whether there are any patterns, i.e. whether add can match."""
        return bool(self._lengths)

    def add(self, path: bytes) -> None:
        """Record PATH under every pattern it matches."""
        # Cheap: it sees every path of a tree's first sample, and most stop here.
        if (0 if path == b"." else path.count(b"/") + 1) not in self._lengths:
            return
        parts = _components(path)
        for pattern, found in zip(self._patterns, self._found, strict=True):
            if len(pattern) == len(parts) and all(
                fnmatchcase(c, p) for c, p in zip(parts, pattern, strict=True)
            ):
                found.add(path)

    def matches(self) -> list[list[bytes]]:
        """Return each pattern's matching paths, sorted."""
        return [sorted(found) for found in self._found]


@dataclass(frozen=True, slots=True)
class Watched:
    """A tree's watched dirs.

    paths: in display order. found: per pattern, how many paths it matched.
    """

    paths: list[bytes]
    found: list[int]


class TooManyWatched(ValueError):
    """More distinct watched dirs (FOUND) than the cap (LIMIT)."""

    def __init__(self, found: int, limit: int):
        super().__init__(f"{found} watched directories, more than {limit}")
        self.found = found
        self.limit = limit


def select(matches: list[list[bytes]], limit: int) -> Watched:
    """Return the watched dirs from MATCHES (per pattern, in config order):
    a path matched by several patterns counts at its first. Raises
    TooManyWatched for more than LIMIT paths."""
    ordered = list(dict.fromkeys(p for found in matches for p in found))
    if len(ordered) > limit:
        raise TooManyWatched(len(ordered), limit)
    return Watched(ordered, [len(found) for found in matches])
