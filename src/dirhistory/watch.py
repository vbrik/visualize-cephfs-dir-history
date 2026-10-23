"""Watch patterns: directories to chart in the page's Watched view.

A watch file lists, per tree, named watch sets of patterns relative to the
tree's root, e.g. `IceCube/*/filtered` under `[tree."/npx/ana".set."Filtered"]`.
The page shows one set at a time: dirs at different depths (`*` and `*/*`)
make poor neighbors. A pattern may hold shell
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

# Default most watched dirs per watch set; more is an error. It guards against a
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


@dataclass(frozen=True, slots=True)
class WatchSet:
    """A tree's watch set: its NAME, as the page shows it, and its PATTERNS."""

    name: str
    patterns: list[WatchPattern]


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


def _sets(tree: str, section: object) -> list[WatchSet]:
    """Return the watch sets of a watch file's `[tree.TREE]` SECTION."""
    if not isinstance(section, dict):
        raise ValueError(f"tree {tree!r} must be a table")
    if "watch" in section:
        raise ValueError(
            f"tree {tree!r}: 'watch' directly in a tree is the old format; put"
            f' it in a named set: [tree."{tree}".set."NAME"]'
        )
    if extra := sorted(set(section) - {"set"}):
        raise ValueError(f"tree {tree!r}: unknown key {extra[0]!r}")
    sets = section.get("set")
    if not isinstance(sets, dict):
        raise ValueError(f"tree {tree!r}: 'set' must be a table of watch sets")
    return [_set(tree, name, s) for name, s in sets.items()]


def _set(tree: str, name: str, section: object) -> WatchSet:
    """Return the watch set NAME of TREE from its SECTION."""
    where = f"tree {tree!r}, set {name!r}"
    if not name.strip():
        raise ValueError(f"tree {tree!r}: a set name must not be blank")
    if not isinstance(section, dict):
        raise ValueError(f"{where} must be a table")
    if extra := sorted(set(section) - {"watch"}):
        raise ValueError(f"{where}: unknown key {extra[0]!r}")
    patterns = section.get("watch")
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ValueError(f"{where}: 'watch' must be a list of strings")
    try:
        return WatchSet(name, [parse_pattern(p) for p in patterns])
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from None


def load(path: Path) -> dict[bytes, list[WatchSet]]:
    """Return the watch sets per tree root of the TOML file at PATH, each
    tree's in file order: `[tree."/fs/path".set."NAME"]` sections, each with
    `watch = ["a/*", ...]`.

    Raises ValueError naming PATH for bad TOML, a top-level key other than
    `tree`, a section that is not as above (the old formats, a top-level
    `watch` or one directly in a tree, get a hint), a blank set name, or a
    bad tree path or pattern; OSError if PATH cannot be read.
    """
    with path.open("rb") as f:
        try:
            doc = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{path}: {e}") from None
    if "watch" in doc:
        raise ValueError(
            f"{path}: a top-level 'watch' is the old format; put the patterns,"
            ' relative to the tree root, in a [tree."/fs/path".set."NAME"] section'
        )
    if extra := sorted(set(doc) - {"tree"}):
        raise ValueError(f"{path}: unknown key {extra[0]!r}; the only key is 'tree'")
    trees = doc.get("tree", {})
    if not isinstance(trees, dict):
        raise ValueError(f"{path}: 'tree' must be a table of trees")
    try:
        return {_tree_root(t): _sets(t, s) for t, s in trees.items()}
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None


class Matcher:
    """Collects, per pattern, the paths that match it.

    PATTERNS are the components (WatchPattern.parts) of the patterns of all
    of a tree's watch sets, one after another. Paths are relative to the
    tree's ROOT (`.` has no components).
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
    """A watch set's watched dirs.

    name: the set's. paths: in display order. found: per pattern, how many
    paths it matched.
    """

    name: str
    paths: list[bytes]
    found: list[int]


class TooManyWatched(ValueError):
    """More distinct watched dirs (FOUND) in the set NAME than the cap (LIMIT)."""

    def __init__(self, name: str, found: int, limit: int):
        super().__init__(
            f"set {name!r}: {found} watched directories, more than {limit}"
        )
        self.name = name
        self.found = found
        self.limit = limit


def select(name: str, matches: list[list[bytes]], limit: int) -> Watched:
    """Return the watched dirs of the set NAME from MATCHES (per pattern, in
    config order): a path matched by several patterns counts at its first.
    Raises TooManyWatched for more than LIMIT paths."""
    ordered = list(dict.fromkeys(p for found in matches for p in found))
    if len(ordered) > limit:
        raise TooManyWatched(name, len(ordered), limit)
    return Watched(name, ordered, [len(found) for found in matches])
