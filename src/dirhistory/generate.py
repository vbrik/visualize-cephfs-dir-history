"""dirhistory-generate: write the website's data files from a trackdir database."""

import argparse
import json
import os
import re
import sys
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import psycopg

from dirhistory import __version__, db, sets
from dirhistory.db import Tree
from dirhistory.history import (
    BUCKET_WIDTHS,
    MAX_POINTS,
    MIN_BUCKET,
    RECENT,
    ROOT,
    Resolution,
    Series,
    TreeHistory,
    parent,
    summarize,
)
from dirhistory.sets import (
    IMPLICIT,
    MAX_DIRS,
    Busiest,
    DirSet,
    SetDirs,
    TooManyDirs,
    with_implicit,
)

INDEX = "trees.json"
# The metrics of a tree file's series pairs, in pair order.
METRICS = ("rbytes", "rentries")

_DURATION = re.compile(r"(\d+)([smhdw])")
_DURATION_UNITS = {
    "s": "seconds",
    "m": "minutes",
    "h": "hours",
    "d": "days",
    "w": "weeks",
}

DESCRIPTION = """\
Write the data files of the CephFS directory history website from a trackdir
database: one tree-ID.json per tree and the index trees.json, replaced
atomically. Run it periodically (e.g. hourly, from a systemd timer); the page
in web/ reads the files.

The sets file (--sets, required; an empty file is valid) names, per tree,
sets of directories. Each tree also gets the implicit set "Top level", its
top-level directories. The page's Stack view charts the rbytes (or, toggled,
the rentries: rfiles + rsubdirs) of a set's directories as bands, and can
split a directory into its subdirectories, down to the depth the set stores.
Its Trends view shows one card per directory of a set.

The database is only read. Prints each file written to stdout; notes go to
stderr.
"""


def parse_duration(text: str) -> timedelta:
    """Return the duration in TEXT: "0", or an integer with a unit s, m, h, d
    or w (e.g. 7d). Raises ValueError otherwise."""
    if text == "0":
        return timedelta(0)
    if m := _DURATION.fullmatch(text):
        return timedelta(**{_DURATION_UNITS[m[2]]: int(m[1])})
    raise ValueError(f"invalid duration {text!r}: use e.g. 30m, 12h, 7d or 2w")


def format_duration(d: timedelta) -> str:
    """Return D as parse_duration reads it, in its largest exact unit up to days."""
    seconds = int(d.total_seconds())
    for unit, size in (("d", 86400), ("h", 3600), ("m", 60)):
        if seconds and seconds % size == 0:
            return f"{seconds // size}{unit}"
    return f"{seconds}s" if seconds else "0"


def resolution(args: argparse.Namespace) -> Resolution:
    """Return the time resolution ARGS ask for."""
    return Resolution(args.recent, args.max_points)


def select_trees(all_trees: Sequence[Tree], ids: Sequence[int]) -> list[Tree]:
    """Return the trees with IDS, or all of them if IDS is empty.

    Raises ValueError naming any id without a tree.
    """
    if not ids:
        return list(all_trees)
    if unknown := sorted(set(ids) - {t.id for t in all_trees}):
        raise ValueError(f"no tree with id {', '.join(map(str, unknown))}")
    return [t for t in all_trees if t.id in ids]


def iso(t: datetime) -> str:
    """Return T in UTC as ISO 8601 to the second, e.g. 2026-10-21T19:19:45Z."""
    return t.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact(series: Series) -> Series | int | None:
    """Return the one value of SERIES if all its points are equal, else
    SERIES. Most stored dirs never change, so this keeps files small."""
    return series[0] if series and len(set(series)) == 1 else series


def tree_document(tree: Tree, history: TreeHistory, generated: datetime) -> dict:
    """Return the JSON document of a tree file.

    Paths are os.fsdecode'd: bytes that are not UTF-8 become lone surrogates,
    which JSON escapes (write_json keeps ensure_ascii). Each stored dir has
    one entry in "series", a pair of its METRICS series, so its path is
    written once: paths are most of a large file. A series that is the same
    at every time point is one number (see compact).
    """
    return {
        "generated": iso(generated),
        "tree": {
            "id": tree.id,
            "fs_path": os.fsdecode(tree.fs_path),
            "fs_name": tree.fs_name,
            "cluster": tree.cluster_fsid,
            "max_depth": tree.max_depth,
        },
        "times": [iso(t) for t in history.times],
        "metrics": list(METRICS),
        "series": {
            os.fsdecode(p): [compact(s), compact(history.rentries[p])]
            for p, s in history.rbytes.items()
        },
        "sets": [
            {"name": s.name, "paths": [os.fsdecode(p) for p in s.paths]}
            for s in history.sets
        ],
        "split": [os.fsdecode(p) for p in history.split],
    }


def index_entry(tree: Tree, history: TreeHistory, generated: datetime) -> dict:
    """Return the trees.json entry of a tree whose file was written at GENERATED."""
    fs = f"filesystem {tree.fs_name}" if tree.fs_name else "default filesystem"
    return {
        "id": tree.id,
        "label": os.fsdecode(tree.fs_path),
        "detail": f"cluster {tree.cluster_fsid[:8]}, {fs}, depth {tree.max_depth}",
        "file": f"tree-{tree.id}.json",
        "last_sample": iso(history.times[-1]),
        "generated": iso(generated),
        "sets": [{"name": s.name, "dirs": len(s.paths)} for s in history.sets],
    }


def write_json(path: Path, doc: object) -> None:
    """Replace PATH with DOC as ASCII JSON, atomically and world-readable
    (subject to the umask), so the web server never sees a partial file."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="ascii") as f:
            json.dump(doc, f, ensure_ascii=True, separators=(",", ":"))
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(tmp, 0o666 & ~umask)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise


def display(path: bytes) -> str:
    """Return PATH for a terminal: bytes that are not UTF-8 as escapes."""
    return path.decode("utf-8", "backslashreplace")


def read_tree(
    conn,
    tree: Tree,
    res: Resolution,
    dir_sets: Sequence[DirSet],
    max_dirs: int,
) -> tuple[TreeHistory, int] | None:
    """Return a tree's history and how many of its directories are in error
    as of its latest sample, or None if it has no samples. Reads in one
    transaction, i.e. one snapshot. DIR_SETS and MAX_DIRS go to summarize
    (which raises TooManyDirs)."""
    with conn.transaction():
        samples = db.samples(conn, tree.id)
        if not samples:
            return None
        errors = db.current_errors(conn, tree.id)
        changed = db.changed_paths(conn, [s.id for s in samples[1:]])
        history = summarize(
            samples,
            changed,
            db.sample_paths(conn, samples[0].id),
            lambda paths: db.path_rows(conn, tree.id, paths),
            res,
            dir_sets,
            max_dirs,
        )
    return history, errors


def busiest_hint(by_depth: Busiest) -> str:
    """Return the dirs of BY_DEPTH (sets.busiest) as lines for a terminal.

    Siblings among a depth's dirs print under their parent, at the rank of
    the first of them, so a run of same-sized siblings reads as one.
    """
    width = max(len(str(n)) for dirs in by_depth.values() for _, n in dirs)
    lines = ["Directories with the most stored subdirectories, by depth:"]
    for depth, dirs in by_depth.items():
        lines.append(f"  depth {depth}:")
        siblings: defaultdict[bytes, list[tuple[bytes, int]]] = defaultdict(list)
        for path, n in dirs:
            siblings[parent(path)].append((path, n))
        for up, group in siblings.items():
            if up == ROOT or len(group) == 1:
                lines += [f"    {n:>{width}}  {display(path)}" for path, n in group]
                continue
            lines.append(f"    {'':>{width}}  {display(up)}/")
            lines += [
                f"    {n:>{width}}    {display(path.rpartition(b'/')[2])}"
                for path, n in group
            ]
    return "\n".join(lines)


def set_warnings(
    tree: Tree, dir_sets: Sequence[DirSet], found: Sequence[SetDirs]
) -> list[str]:
    """Return a warning per pattern of TREE's DIR_SETS that matched no
    directory (FOUND, per set, counts each pattern's matches). The implicit
    set never warns: it is no one's mistake if a tree has no subdirectories."""
    return [
        f"WARNING: tree {tree.id} ({display(tree.fs_path)}): set"
        f" {s.name!r}: pattern {p.text!r} matches no directory"
        for s, f in zip(dir_sets, found, strict=True)
        if s is not IMPLICIT
        for (p, _), n in zip(s.patterns(), f.found, strict=True)
        if not n
    ]


def tree_sets(
    config: Mapping[bytes, Sequence[DirSet]], all_trees: Sequence[Tree]
) -> dict[int, list[DirSet]]:
    """Return the sets by tree id, for every tree of ALL_TREES: each section
    of CONFIG (by tree root) goes to the tree with that fs_path, and every
    tree has the implicit set (sets.with_implicit).

    Raises ValueError for a section without a tree, or with several trees
    (tree roots are assumed unique, so they cannot be told apart).
    """
    by_tree: dict[int, list[DirSet]] = {t.id: with_implicit([]) for t in all_trees}
    for root, dir_sets in config.items():
        ids = [t.id for t in all_trees if t.fs_path == root]
        if not ids:
            raise ValueError(f"--sets: no tree has root {display(root)}")
        if len(ids) > 1:
            raise ValueError(
                f"--sets: trees {', '.join(map(str, ids))} all have root"
                f" {display(root)}, so it cannot name one"
            )
        by_tree[ids[0]] = with_implicit(dir_sets)
    return by_tree


def merged_index(out: Path, entries: list[dict], all_trees: Sequence[Tree]) -> list:
    """Return trees.json's new entries: ENTRIES, plus those of the existing
    index for trees not in ENTRIES that still exist, by tree id."""
    try:
        old = json.loads((out / INDEX).read_text(encoding="ascii"))
    except FileNotFoundError:
        old = []
    fresh = {e["id"] for e in entries}
    live = {t.id for t in all_trees}
    kept = [e for e in old if e["id"] in live and e["id"] not in fresh]
    return sorted(kept + entries, key=lambda e: e["id"])


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dirhistory-generate",
        description=DESCRIPTION,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--db",
        default=os.environ.get("TRACKDIR_DB"),
        metavar="URL",
        help="PostgreSQL URL (default: $TRACKDIR_DB); put the password in"
        " ~/.pgpass or $PGPASSWORD, not here",
    )
    p.add_argument(
        "--out",
        type=Path,
        default=Path("web/data"),
        metavar="DIR",
        help="directory for the data files (default: %(default)s)",
    )
    p.add_argument(
        "--sets",
        type=Path,
        required=True,
        metavar="FILE",
        help="TOML file of named sets of directories per tree:"
        ' [tree."/tree/root".set."NAME"] dirs = [{ match = ["dir/*"], depth = N }];'
        " see deploy/sets.toml.example",
    )
    p.add_argument(
        "--max-dirs",
        type=_positive_int,
        default=MAX_DIRS,
        metavar="N",
        help="fail if a tree's sets store more directories than this, counting"
        " the ones depth adds (default: %(default)s)",
    )
    p.add_argument(
        "--recent",
        type=_duration_arg,
        default=format_duration(RECENT),
        metavar="DURATION",
        help=f"keep this much recent history at {format_duration(MIN_BUCKET)}"
        " resolution; older history gets coarser buckets to fit --max-points."
        " 0: one resolution for the whole history (default: %(default)s)",
    )
    p.add_argument(
        "--max-points",
        type=_positive_int,
        default=MAX_POINTS,
        metavar="N",
        help="at most this many time points per tree; history gets the finest"
        f" bucket of {', '.join(map(format_duration, BUCKET_WIDTHS))} (or a"
        " multiple of the last) that fits (default: %(default)s)",
    )
    p.add_argument(
        "--tree",
        type=int,
        action="append",
        default=[],
        metavar="ID",
        help="only this tree; repeatable (default: all trees)",
    )
    p.add_argument("-v", "--verbose", action="store_true", help="report per-tree stats")
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def _duration_arg(text: str) -> timedelta:
    try:
        return parse_duration(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _positive_int(text: str) -> int:
    if text.isdigit() and int(text) > 0:
        return int(text)
    raise argparse.ArgumentTypeError(f"not a positive integer: {text!r}")


def main(argv: Sequence[str] | None = None) -> int:
    p = parser()
    args = p.parse_args(argv)
    res = resolution(args)
    if res.recent_points() >= res.max_points:
        p.error(
            f"--recent {format_duration(res.recent)} at"
            f" {format_duration(MIN_BUCKET)} needs up to"
            f" {res.recent_points()} points, but --max-points is {res.max_points}:"
            " leave room for older history by raising --max-points or shortening"
            " --recent"
        )
    if not args.db:
        return _error("no database: pass --db or set TRACKDIR_DB")
    try:
        return run(args, sets.load(args.sets))
    except (db.DbError, psycopg.Error, OSError, ValueError) as e:
        return _error(str(e).strip())


def run(args: argparse.Namespace, config: Mapping[bytes, Sequence[DirSet]]) -> int:
    """Write the data files ARGS ask for, with CONFIG's sets (by tree root,
    as sets.load returns them); return the exit status.

    Raises DbError, psycopg.Error, OSError, or ValueError for a bad --tree,
    a CONFIG section that names no single tree, or a tree whose sets store
    more directories than --max-dirs.
    """
    conn = db.connect(args.db)
    db.check_schema(conn)
    all_trees = db.trees(conn)
    dir_sets = tree_sets(config, all_trees)
    chosen = select_trees(all_trees, args.tree)
    args.out.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(UTC)
    entries = []
    with conn:
        for tree in chosen:
            start = perf_counter()
            try:
                result = read_tree(
                    conn, tree, resolution(args), dir_sets[tree.id], args.max_dirs
                )
            except TooManyDirs as e:
                per_set = ", ".join(f"{name}: {n}" for name, n in e.counts)
                raise ValueError(
                    f"tree {tree.id} ({display(tree.fs_path)}): its sets store"
                    f" {e.found} directories, more than --max-dirs {e.limit}"
                    f" ({per_set}). Lower their depths, narrow their patterns or"
                    " raise --max-dirs."
                    + (f"\n{busiest_hint(e.busiest)}" if e.busiest else "")
                ) from None
            if result is None:
                _note(
                    f"tree {tree.id} ({display(tree.fs_path)}) has no samples; skipped"
                )
                continue
            history, errors = result
            path = args.out / f"tree-{tree.id}.json"
            write_json(path, tree_document(tree, history, generated))
            entries.append(index_entry(tree, history, generated))
            print(path)
            if errors:
                _note(
                    f"WARNING: tree {tree.id} ({display(tree.fs_path)}): {errors}"
                    " directories could not be read in its latest sample. Below them,"
                    " changes go unseen and sizes may be stale."
                )
            for warning in set_warnings(tree, dir_sets[tree.id], history.sets):
                _note(warning)
            if args.verbose:
                _note(
                    f"tree {tree.id} ({display(tree.fs_path)}):"
                    f" {len(history.times)} points, {len(history.rbytes)} stored"
                    f" dirs in {_count(len(history.sets), 'set')},"
                    f" {perf_counter() - start:.1f} s"
                )
    index = args.out / INDEX
    write_json(index, merged_index(args.out, entries, all_trees))
    print(index)
    return 0


def _count(n: int, noun: str) -> str:
    """Return N NOUNs, e.g. "1 set" or "2 sets"."""
    return f"{n} {noun}{'' if n == 1 else 's'}"


def _note(message: str) -> None:
    print(message, file=sys.stderr)


def _error(message: str) -> int:
    print(f"ERROR: {message}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
