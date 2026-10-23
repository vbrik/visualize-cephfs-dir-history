"""Watch patterns: directories to chart in the page's Watched view.

A watch pattern is an absolute path whose components may hold shell
wildcards (`*`, `?`, `[...]`), e.g. `/npx/ana/IceCube/*/filtered`. It is
matched component by component, so `*` never crosses `/`, and a match has
exactly as many components as the pattern. There is no `**`: trees are
sampled to a max depth anyway, and a recursive pattern could watch
thousands of directories by accident.
"""

import os
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

# Default cap of watched dirs per tree: each is one card, i.e. one chart, on
# the page.
MAX_WATCHED = 100

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

    def for_tree(self, fs_path: bytes) -> Parts | None:
        """Return the components below FS_PATH, or None if the pattern does
        not apply to the tree at FS_PATH: its first components must match all
        of FS_PATH's."""
        tree = _components(fs_path)
        if len(self.parts) < len(tree):
            return None
        if all(fnmatchcase(t, p) for t, p in zip(tree, self.parts, strict=False)):
            return self.parts[len(tree) :]
        return None


def parse_pattern(text: str) -> WatchPattern:
    """Return the watch pattern in TEXT. Raises ValueError naming TEXT unless
    it is absolute, with no empty, `.`, `..` or `**` component."""
    if not text.startswith("/"):
        raise ValueError(f"watch pattern {text!r} is not an absolute path")
    if text == "/":
        return WatchPattern(text, ())
    parts = text[1:].split("/")
    if bad := {"", ".", "..", "**"} & set(parts):
        what = "an empty" if "" in bad else f"a {sorted(bad)[0]!r}"
        raise ValueError(f"watch pattern {text!r} has {what} component")
    return WatchPattern(text, tuple(os.fsencode(p) for p in parts))


def load(path: Path) -> list[WatchPattern]:
    """Return the patterns of the TOML file at PATH: `watch = ["/a/*", ...]`.

    Raises ValueError naming PATH for bad TOML, keys other than `watch`, a
    `watch` that is not a list of strings, or a bad pattern; OSError if PATH
    cannot be read.
    """
    with path.open("rb") as f:
        try:
            doc = tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ValueError(f"{path}: {e}") from None
    if extra := sorted(set(doc) - {"watch"}):
        raise ValueError(f"{path}: unknown key {extra[0]!r}; the only key is 'watch'")
    patterns = doc.get("watch")
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise ValueError(f"{path}: 'watch' must be a list of strings")
    try:
        return [parse_pattern(p) for p in patterns]
    except ValueError as e:
        raise ValueError(f"{path}: {e}") from None


class Matcher:
    """Collects, per pattern, the paths that match it.

    PATTERNS are components below a tree's ROOT (WatchPattern.for_tree);
    None never matches. Paths are relative to ROOT (`.` has no components).
    """

    def __init__(self, patterns: Sequence[Parts | None]):
        self._patterns = patterns
        self._lengths = {len(p) for p in patterns if p is not None}
        self._found: list[set[bytes]] = [set() for _ in patterns]

    def add(self, path: bytes) -> None:
        """Record PATH under every pattern it matches."""
        parts = _components(path)
        if len(parts) not in self._lengths:  # cheap: most paths stop here
            return
        for pattern, found in zip(self._patterns, self._found, strict=True):
            if (
                pattern is not None
                and len(pattern) == len(parts)
                and all(fnmatchcase(c, p) for c, p in zip(parts, pattern, strict=True))
            ):
                found.add(path)

    def matches(self) -> list[list[bytes]]:
        """Return each pattern's matching paths, sorted."""
        return [sorted(found) for found in self._found]


@dataclass(frozen=True, slots=True)
class Watched:
    """A tree's watched dirs.

    paths: in display order, at most the cap. found: per pattern, how many
    paths it matched. dropped: how many distinct matches the cap cut.
    """

    paths: list[bytes]
    found: list[int]
    dropped: int


def select(matches: list[list[bytes]], limit: int) -> Watched:
    """Return the watched dirs from MATCHES (per pattern, in config order):
    a path matched by several patterns counts at its first, and only the
    first LIMIT paths are kept."""
    ordered = list(dict.fromkeys(p for found in matches for p in found))
    return Watched(
        ordered[:limit], [len(found) for found in matches], max(len(ordered) - limit, 0)
    )
