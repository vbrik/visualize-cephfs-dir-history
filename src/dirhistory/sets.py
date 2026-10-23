"""Sets of directories to chart, from the sets file.

A sets file lists, per tree, named sets of directories, e.g.
`[tree."/npx/ana".set."Filtered"]`. Each set holds groups of patterns, each
group with a `depth`: how many levels below every matched directory are
stored too. Both views draw from the stored directories: Stack splits and
drills by them, Trends shows a card for each of the current set's. The
largest `depth` of the groups (of any set) that cover a directory decides
how deep it is stored, so the file need not record it per set.

A pattern is relative to the tree's root and may hold shell wildcards (`*`,
`?`, `[...]`). It is matched component by component, so `*` never crosses
`/`, and a match has exactly as many components as the pattern. `.` is the
tree root itself. There is no `**`: use `depth` to go deeper.

Every tree also has the implicit set `Top level` (`*` at depth 0), first;
a set of that name in the file replaces it.

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

IMPLICIT_NAME = "Top level"

# Default most stored dirs per tree; more is an error. It guards a per-tree
# file against a depth or pattern that stores far more than meant.
MAX_DIRS = 20_000

# Path components, as bytes.
type Parts = tuple[bytes, ...]


def _components(path: bytes) -> Parts:
    """Return the components of an absolute or ROOT-relative PATH."""
    return tuple(c for c in path.split(b"/") if c and c != b".")


@dataclass(frozen=True, slots=True)
class Pattern:
    """A pattern as written (TEXT) and its components (PARTS)."""

    text: str
    parts: Parts


@dataclass(frozen=True, slots=True)
class DirGroup:
    """PATTERNS whose matches are stored DEPTH levels deep (0: only them)."""

    patterns: list[Pattern]
    depth: int


@dataclass(frozen=True, slots=True)
class DirSet:
    """A tree's set: its NAME, as the page shows it, and its GROUPS."""

    name: str
    groups: list[DirGroup]

    def patterns(self) -> list[tuple[Pattern, int]]:
        """Return each pattern with its group's depth, in file order."""
        return [(p, g.depth) for g in self.groups for p in g.patterns]


def _split(text: str, banned: set[str], what: str) -> list[str]:
    """Return the components of the slash-separated TEXT. Raises ValueError
    naming TEXT (as WHAT) for an empty component or one in BANNED."""
    parts = text.split("/")
    if bad := ({""} | banned) & set(parts):
        which = "an empty" if "" in bad else f"a {sorted(bad)[0]!r}"
        raise ValueError(f"{what} {text!r} has {which} component")
    return parts


def parse_pattern(text: str) -> Pattern:
    """Return the pattern in TEXT: a path relative to the tree root, or `.`
    for the root. Raises ValueError naming TEXT if it is absolute or has an
    empty, `.`, `..` or `**` component."""
    if text.startswith("/"):
        raise ValueError(f"pattern {text!r} is not relative to the tree root")
    if text == ".":
        return Pattern(text, ())
    parts = _split(text, {".", "..", "**"}, "pattern")
    return Pattern(text, tuple(os.fsencode(p) for p in parts))


IMPLICIT = DirSet(IMPLICIT_NAME, [DirGroup([parse_pattern("*")], 0)])


def with_implicit(sets: Sequence[DirSet]) -> list[DirSet]:
    """Return SETS with the set named `Top level` first: the one in SETS if
    any, else IMPLICIT."""
    top = next((s for s in sets if s.name == IMPLICIT_NAME), IMPLICIT)
    return [top, *(s for s in sets if s is not top)]


def _tree_root(text: str) -> bytes:
    """Return the tree root path in TEXT. Raises ValueError naming TEXT unless
    it is absolute, with no empty, `.` or `..` component (`/` is the root)."""
    if text == "/":
        return b"/"
    if not text.startswith("/"):
        raise ValueError(f"tree {text!r} is not an absolute path")
    _split(text[1:], {".", ".."}, "tree")
    return os.fsencode(text)


def _sets(tree: str, section: object) -> list[DirSet]:
    """Return the sets of a sets file's `[tree.TREE]` SECTION."""
    if not isinstance(section, dict):
        raise ValueError(f"tree {tree!r} must be a table")
    if "watch" in section:
        raise ValueError(
            f"tree {tree!r}: 'watch' directly in a tree is the old format; put"
            f' dirs in a named set: [tree."{tree}".set."NAME"]'
        )
    if extra := sorted(set(section) - {"set"}):
        raise ValueError(f"tree {tree!r}: unknown key {extra[0]!r}")
    sets = section.get("set")
    if not isinstance(sets, dict):
        raise ValueError(f"tree {tree!r}: 'set' must be a table of sets")
    return [_set(tree, name, s) for name, s in sets.items()]


def _group(where: str, group: object) -> DirGroup:
    """Return the DirGroup in GROUP, a `{ match = [...], depth = N }` table.
    Raises ValueError starting with WHERE if it is not."""
    if not isinstance(group, dict):
        raise ValueError(f"{where}: each of 'dirs' must be a table")
    if extra := sorted(set(group) - {"match", "depth"}):
        raise ValueError(f"{where}: unknown key {extra[0]!r} in a group of 'dirs'")
    match, depth = group.get("match"), group.get("depth")
    if (
        not isinstance(match, list)
        or not match
        or not all(isinstance(p, str) for p in match)
    ):
        raise ValueError(f"{where}: 'match' must be a non-empty list of strings")
    # bool is an int in Python, but `depth = true` is a mistake.
    if not isinstance(depth, int) or isinstance(depth, bool) or depth < 0:
        raise ValueError(f"{where}: 'depth' must be an integer >= 0")
    try:
        return DirGroup([parse_pattern(p) for p in match], depth)
    except ValueError as e:
        raise ValueError(f"{where}: {e}") from None


def _set(tree: str, name: str, section: object) -> DirSet:
    """Return the set NAME of TREE from its SECTION."""
    where = f"tree {tree!r}, set {name!r}"
    if not name.strip():
        raise ValueError(f"tree {tree!r}: a set name must not be blank")
    if not isinstance(section, dict):
        raise ValueError(f"{where} must be a table")
    if "watch" in section:
        raise ValueError(
            f"{where}: 'watch' is the old format; use"
            " dirs = [{ match = [...], depth = N }]"
        )
    if extra := sorted(set(section) - {"dirs"}):
        raise ValueError(f"{where}: unknown key {extra[0]!r}")
    groups = section.get("dirs")
    if not isinstance(groups, list) or not groups:
        raise ValueError(f"{where}: 'dirs' must be a non-empty list of groups")
    return DirSet(name, [_group(where, g) for g in groups])


def load(path: Path) -> dict[bytes, list[DirSet]]:
    """Return the sets per tree root of the TOML file at PATH, each tree's in
    file order: `[tree."/fs/path".set."NAME"]` sections, each with
    `dirs = [{ match = ["a/*", ...], depth = N }, ...]`. The implicit set is
    not added; see with_implicit.

    Raises ValueError naming PATH for bad TOML, a top-level key other than
    `tree`, a section that is not as above (the old `watch` formats get a
    hint), a blank set name, a bad `depth`, or a bad tree path or pattern;
    OSError if PATH cannot be read.
    """
    with path.open("rb") as f:
        try:
            doc = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{path}: {e}") from None
    if "watch" in doc:
        raise ValueError(
            f"{path}: a top-level 'watch' is the old format; put the dirs,"
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
    """Collects the paths covered by a tree's sets.

    A pattern of P components with depth D covers a path of L components when
    P <= L <= P + D and the path's first P components match; it matches the
    path exactly when L == P. SETS' patterns are all matched; paths are
    relative to the tree's ROOT (`.` has no components).
    """

    def __init__(self, sets: Sequence[DirSet]):
        # (set, pattern within its set, pattern components, depth)
        self._entries = [
            (i, j, p.parts, depth)
            for i, s in enumerate(sets)
            for j, (p, depth) in enumerate(s.patterns())
        ]
        # Bounds of the covered path lengths; the cheap first filter in add.
        self._lo = min((len(parts) for _, _, parts, _ in self._entries), default=0)
        self._hi = max((len(parts) + d for _, _, parts, d in self._entries), default=-1)
        self._matches = [[set() for _ in s.patterns()] for s in sets]
        self._covered: list[set[bytes]] = [set() for _ in sets]
        self._stored: dict[bytes, int] = {}

    @property
    def wants(self) -> bool:
        """Whether there are any patterns, i.e. whether add can match."""
        return bool(self._entries)

    def add(self, path: bytes) -> None:
        """Record PATH under every pattern that matches or covers it."""
        # Cheap: it sees every path of a tree's first sample, and most stop here.
        length = 0 if path == b"." else path.count(b"/") + 1
        if not self._lo <= length <= self._hi:
            return
        parts = _components(path)
        for i, j, pattern, depth in self._entries:
            n = len(pattern)
            if n <= length <= n + depth and all(
                fnmatchcase(c, p) for c, p in zip(parts, pattern, strict=False)
            ):
                self._covered[i].add(path)
                self._stored[path] = max(
                    self._stored.get(path, 0), depth - (length - n)
                )
                if length == n:
                    self._matches[i][j].add(path)

    def matches(self) -> list[list[list[bytes]]]:
        """Return, per set and pattern, the paths that match it exactly, sorted."""
        return [[sorted(found) for found in s] for s in self._matches]

    def stored(self) -> dict[bytes, int]:
        """Return every path within depth of a match, with the most levels
        below it that are stored too (>= 0): the largest depth left over all
        patterns that cover it."""
        return dict(self._stored)

    def counts(self) -> list[int]:
        """Return, per set, how many distinct stored paths its patterns cover."""
        return [len(covered) for covered in self._covered]


@dataclass(frozen=True, slots=True)
class SetDirs:
    """A set's dirs.

    name: the set's. paths: in display order. found: per pattern, how many
    paths it matched.
    """

    name: str
    paths: list[bytes]
    found: list[int]


def select(name: str, matches: list[list[bytes]]) -> SetDirs:
    """Return the dirs of the set NAME from MATCHES (per pattern, in file
    order): a path matched by several patterns counts at its first."""
    ordered = list(dict.fromkeys(p for found in matches for p in found))
    return SetDirs(name, ordered, [len(found) for found in matches])


class TooManyDirs(ValueError):
    """More distinct stored dirs (FOUND) than the cap (LIMIT). COUNTS are the
    dirs each set covers, as (set name, count)."""

    def __init__(self, found: int, limit: int, counts: list[tuple[str, int]]):
        per_set = ", ".join(f"{name!r} {n}" for name, n in counts)
        super().__init__(
            f"{found} directories to store, more than {limit}; per set: {per_set}"
        )
        self.found = found
        self.limit = limit
        self.counts = counts
