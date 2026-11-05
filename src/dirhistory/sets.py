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

Every tree has a budget: the most dirs its file stores. A tree's own
`budget` overrides the file's top-level one; a tree with neither is an
error, as is a file with no trees.

Trees are named by their root path, which is assumed to be unique among
trees: two trees with the same path (on different clusters or filesystems)
cannot be told apart.
"""

import os
import tomllib
from collections import defaultdict
from collections.abc import Container, Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

IMPLICIT_NAME = "Top level"

# Path components, as bytes.
type Parts = tuple[bytes, ...]

# The tree root, as paths relative to it name it.
ROOT = b"."


def parent(path: bytes) -> bytes:
    """Return the parent of PATH, which must not be ROOT."""
    head, sep, _ = path.rpartition(b"/")
    return head if sep else ROOT


def level(path: bytes) -> int:
    """Return how many components ROOT-relative PATH has (ROOT: 0)."""
    return 0 if path == ROOT else path.count(b"/") + 1


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


@dataclass(frozen=True, slots=True)
class TreeConfig:
    """A tree's section of the sets file: its BUDGET (the most dirs its file
    stores, resolved against the file's default) and its SETS, in file
    order, without the implicit set."""

    budget: int
    sets: list[DirSet]


def _budget(where: str, value: object) -> int:
    """Return VALUE, a `budget`. Raises ValueError starting with WHERE unless
    it is an integer >= 1."""
    # bool is an int in Python, but `budget = true` is a mistake.
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{where}: 'budget' must be an integer >= 1")
    return value


def _tree(tree: str, section: object, default: int | None) -> TreeConfig:
    """Return the TreeConfig of a sets file's `[tree.TREE]` SECTION, whose
    budget is DEFAULT (the file's) unless the section has its own."""
    if not isinstance(section, dict):
        raise ValueError(f"tree {tree!r} must be a table")
    budget = default
    if "budget" in section:
        budget = _budget(f"tree {tree!r}", section["budget"])
    dir_sets = _sets(tree, section)
    if budget is None:
        raise ValueError(
            f'tree {tree!r} has no budget: add budget = N to its [tree."{tree}"]'
            " section, or at the top of the file for every tree"
        )
    return TreeConfig(budget, dir_sets)


def _sets(tree: str, section: dict) -> list[DirSet]:
    """Return the sets of a sets file's `[tree.TREE]` SECTION."""
    if "watch" in section:
        raise ValueError(
            f"tree {tree!r}: 'watch' directly in a tree is the old format; put"
            f' dirs in a named set: [tree."{tree}".set."NAME"]'
        )
    if extra := sorted(set(section) - {"set", "budget"}):
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


def load(path: Path) -> dict[bytes, TreeConfig]:
    """Return the config per tree root of the TOML file at PATH: a top-level
    `budget = N` (optional), and per tree an optional `budget` and its sets,
    in file order: `[tree."/fs/path".set."NAME"]` sections, each with
    `dirs = [{ match = ["a/*", ...], depth = N }, ...]`. The implicit set is
    not added; see with_implicit.

    Raises ValueError naming PATH for bad TOML, a top-level key other than
    `tree` and `budget`, no trees, a tree without a budget (its own or the
    file's), a section that is not as above (the old `watch` formats get a
    hint), a blank set name, a bad `depth` or `budget`, or a bad tree path
    or pattern; OSError if PATH cannot be read.
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
    if extra := sorted(set(doc) - {"tree", "budget"}):
        raise ValueError(
            f"{path}: unknown key {extra[0]!r}; the only keys are 'tree' and 'budget'"
        )
    trees = doc.get("tree", {})
    if not isinstance(trees, dict):
        raise ValueError(f"{path}: 'tree' must be a table of trees")
    if not trees:
        raise ValueError(
            f'{path}: no trees: add a [tree."/fs/path".set."NAME"] section per tree'
            " to generate"
        )
    try:
        default = _budget("top level", doc["budget"]) if "budget" in doc else None
        return {_tree_root(t): _tree(t, s, default) for t, s in trees.items()}
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
        self._stored: dict[bytes, int] = {}

    @property
    def wants(self) -> bool:
        """Whether there are any patterns, i.e. whether add can match."""
        return bool(self._entries)

    def add(self, path: bytes) -> None:
        """Record PATH under every pattern that matches or covers it."""
        # Cheap: it sees every path of a tree's first sample, and most stop here.
        length = level(path)
        if not self._lo <= length <= self._hi:
            return
        parts = _components(path)
        for i, j, pattern, depth in self._entries:
            n = len(pattern)
            if n <= length <= n + depth and all(
                fnmatchcase(c, p) for c, p in zip(parts, pattern, strict=False)
            ):
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


@dataclass(frozen=True, slots=True)
class SetDirs:
    """A set's dirs.

    name: the set's. paths: in display order. found: per pattern, how many
    paths it matched, kept or not. dropped: how many of its dirs the budget
    left out (see cut).
    """

    name: str
    paths: list[bytes]
    found: list[int]
    dropped: int = 0


def select(name: str, matches: list[list[bytes]], kept: Container[bytes]) -> SetDirs:
    """Return the dirs of the set NAME from MATCHES (per pattern, in file
    order) that are in KEPT: a path matched by several patterns counts at
    its first."""
    ordered = list(dict.fromkeys(p for found in matches for p in found))
    paths = [p for p in ordered if p in kept]
    return SetDirs(
        name, paths, [len(found) for found in matches], len(ordered) - len(paths)
    )


@dataclass(frozen=True, slots=True)
class Cut:
    """What cut kept of a tree's stored dirs.

    kept: the kept paths, with their depth left (as Matcher.stored). pruned:
    the kept dirs that would have been split but are not, sorted. level: the
    partial level, the shallowest with a dir left out; None if none is.
    """

    kept: dict[bytes, int]
    pruned: list[bytes]
    level: int | None


def cut(stored: Mapping[bytes, int], budget: int) -> Cut:
    """Return the dirs of STORED (Matcher.stored) to keep within BUDGET,
    breadth-first from the tree root.

    Levels are kept whole, shallowest first, while they fit. The first level
    that does not fit is filled by units in byte order of their first path,
    up to the first unit that does not fit; nothing deeper is kept. A unit is
    all the stored subdirs of a stored dir, so every kept dir has all of its
    stored subdirs kept or none: a partly kept dir could neither be split
    (the page needs all its subdirs) nor be told from a leaf. A dir whose
    parent is not stored (a set dir) is a unit of its own.

    A kept dir is pruned if it is stored with levels below it (depth left
    >= 1, so without the cut it would be split) and its subdirs were left
    out. A dir stored only for itself, whose subdir is a set dir of its own,
    is not: raising the budget would not make it splittable.
    """
    by_level: defaultdict[int, list[bytes]] = defaultdict(list)
    for path in stored:
        by_level[level(path)].append(path)
    kept: dict[bytes, int] = {}
    left = budget
    partial = None
    for lvl in sorted(by_level):
        paths = by_level[lvl]
        if len(paths) <= left:
            kept.update((p, stored[p]) for p in paths)
            left -= len(paths)
            continue
        partial = lvl
        units: dict[bytes, list[bytes]] = {}
        for path in sorted(paths):
            up = parent(path) if path != ROOT else path
            units.setdefault(up if up in stored else path, []).append(path)
        for unit in units.values():
            if len(unit) > left:
                break
            kept.update((p, stored[p]) for p in unit)
            left -= len(unit)
        break
    pruned = {
        up
        for path in stored.keys() - kept.keys()
        if (up := parent(path)) in kept and stored[up] >= 1
    }
    return Cut(kept, sorted(pruned), partial)
