"""dirhistory-generate: write the website's data files from a trackdir database."""

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter

import psycopg

from dirhistory import __version__, db, watch
from dirhistory.db import Tree
from dirhistory.history import (
    MAX_POINTS,
    MIN_BUCKET,
    RECENT,
    Resolution,
    TreeHistory,
    summarize,
)
from dirhistory.watch import MAX_WATCHED, TooManyWatched, Watched, WatchSet

DEFAULT_THRESHOLD = "10GiB"
DEFAULT_MAX_UNITS = 1000
INDEX = "trees.json"

_SIZE = re.compile(r"(\d+(?:\.\d+)?)\s*([kmgtp]i)b", re.IGNORECASE)
_POWERS = {"k": 1, "m": 2, "g": 3, "t": 4, "p": 5}
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

Charts stack the *own size* of *units*. A directory's own size is its rbytes
minus its subdirectories' rbytes: the bytes of the files directly in it. A
directory without sampled subdirectories (none, or at the tree's max depth)
owns its whole rbytes. A unit is a directory whose own size changed by at
least --threshold over the tree's history. Own sizes never overlap, so units
never count the same bytes twice.

The page's Watched view charts the whole size (rbytes) of the directories
that the --watch file names for each tree, one card each. A tree's directories
come in named watch sets, which the page shows one at a time.

The database is only read. Prints each file written to stdout; notes go to
stderr.
"""


def parse_size(text: str) -> int:
    """Return the bytes in TEXT: an integer, or a number with a binary unit
    (KiB, MiB, GiB, TiB, PiB; any case). Raises ValueError otherwise."""
    if text.isdigit():
        return int(text)
    if m := _SIZE.fullmatch(text.strip()):
        return int(float(m[1]) * 1024 ** _POWERS[m[2][0].lower()])
    raise ValueError(f"invalid size {text!r}: use bytes or a unit, e.g. 10GiB")


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
    return Resolution(args.recent, args.max_points, args.min_bucket)


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


def tree_document(
    tree: Tree, history: TreeHistory, threshold: int, generated: datetime
) -> dict:
    """Return the JSON document of a tree file.

    Paths are os.fsdecode'd: bytes that are not UTF-8 become lone surrogates,
    which JSON escapes (write_json keeps ensure_ascii).
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
        "threshold": threshold,
        "times": [iso(t) for t in history.times],
        "rbytes": {os.fsdecode(p): s for p, s in history.rbytes.items()},
        "units": {
            os.fsdecode(p): {"own": u.own, "leaf": u.leaf}
            for p, u in history.units.items()
        },
        "watched": [
            {"name": w.name, "paths": [os.fsdecode(p) for p in w.paths]}
            for w in history.watched
        ],
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
        "units": len(history.units),
        "watched": [{"name": w.name, "dirs": len(w.paths)} for w in history.watched],
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
    threshold: int,
    limit: int,
    res: Resolution,
    watch: Sequence[WatchSet] = (),
    watch_limit: int = MAX_WATCHED,
) -> tuple[TreeHistory, int] | None:
    """Return a tree's history and how many of its directories are in error
    as of its latest sample, or None if it has no samples. Reads in one
    transaction, i.e. one snapshot. WATCH and WATCH_LIMIT go to summarize."""
    with conn.transaction():
        samples = db.samples(conn, tree.id)
        if not samples:
            return None
        errors = db.current_errors(conn, tree.id)
        changed = db.changed_paths(conn, [s.id for s in samples[1:]])
        history = summarize(
            samples,
            changed,
            db.sample_rows(conn, samples[0].id),
            lambda paths: db.path_rows(conn, tree.id, paths),
            threshold,
            limit,
            res,
            watch,
            watch_limit,
        )
    return history, errors


def watch_warnings(
    tree: Tree, sets: Sequence[WatchSet], watched: Sequence[Watched]
) -> list[str]:
    """Return a warning per pattern of TREE's watch SETS that matched no
    directory (WATCHED, per set, counts each pattern's matches)."""
    return [
        f"WARNING: tree {tree.id} ({display(tree.fs_path)}): watch set"
        f" {s.name!r}: pattern {p.text!r} matches no directory"
        for s, w in zip(sets, watched, strict=True)
        for p, n in zip(s.patterns, w.found, strict=True)
        if not n
    ]


def tree_sets(
    config: Mapping[bytes, Sequence[WatchSet]], all_trees: Sequence[Tree]
) -> dict[int, Sequence[WatchSet]]:
    """Return the watch sets by tree id: each section of CONFIG (by tree
    root) goes to the tree of ALL_TREES with that fs_path.

    Raises ValueError for a section without a tree, or with several trees
    (tree roots are assumed unique, so they cannot be told apart).
    """
    by_tree: dict[int, Sequence[WatchSet]] = {}
    for root, sets in config.items():
        ids = [t.id for t in all_trees if t.fs_path == root]
        if not ids:
            raise ValueError(f"--watch: no tree has root {display(root)}")
        if len(ids) > 1:
            raise ValueError(
                f"--watch: trees {', '.join(map(str, ids))} all have root"
                f" {display(root)}, so it cannot name one"
            )
        by_tree[ids[0]] = sets
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
        "--threshold",
        type=_size_arg,
        default=DEFAULT_THRESHOLD,
        metavar="SIZE",
        help="own-size change that makes a directory a unit (default: %(default)s)",
    )
    p.add_argument(
        "--max-units",
        type=_positive_int,
        metavar="N",
        default=DEFAULT_MAX_UNITS,
        help="keep at most this many units per tree, largest change first"
        " (default: %(default)s)",
    )
    p.add_argument(
        "--recent",
        type=_duration_arg,
        default=format_duration(RECENT),
        metavar="DURATION",
        help="keep this much recent history at --min-bucket resolution"
        " (default: %(default)s)",
    )
    p.add_argument(
        "--max-points",
        type=_positive_int,
        default=MAX_POINTS,
        metavar="N",
        help="at most this many time points per tree; older history gets"
        " coarser buckets to fit (default: %(default)s)",
    )
    p.add_argument(
        "--min-bucket",
        type=_positive_duration_arg,
        default=format_duration(MIN_BUCKET),
        metavar="DURATION",
        help="finest time bucket; below 1h, rbytes propagation lag can make"
        " ghost units (default: %(default)s)",
    )
    p.add_argument(
        "--watch",
        type=Path,
        metavar="FILE",
        help="TOML file of directories to chart in the page's Watched view,"
        ' in named sets per tree: [tree."/tree/root".set."NAME"] watch ='
        ' ["dir/*", ...], paths below the tree root whose components may hold'
        " wildcards (*, ?, [...]); see deploy/watch.toml.example (default: none)",
    )
    p.add_argument(
        "--max-watched",
        type=_positive_int,
        metavar="N",
        default=MAX_WATCHED,
        help="fail if a --watch set's patterns match more than this many dirs"
        " (default: %(default)s)",
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


def _size_arg(text: str) -> int:
    try:
        size = parse_size(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None
    if size == 0:
        raise argparse.ArgumentTypeError("must be more than 0")
    return size


def _duration_arg(text: str) -> timedelta:
    try:
        return parse_duration(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def _positive_duration_arg(text: str) -> timedelta:
    if (d := _duration_arg(text)) > timedelta(0):
        return d
    raise argparse.ArgumentTypeError("must be more than 0")


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
            f"--recent {format_duration(res.recent)} at --min-bucket"
            f" {format_duration(res.min_bucket)} needs up to {res.recent_points()}"
            f" points, but --max-points is {res.max_points}: leave room for older"
            " history by raising --max-points or --min-bucket, or shortening --recent"
        )
    if not args.db:
        return _error("no database: pass --db or set TRACKDIR_DB")
    try:
        config = watch.load(args.watch) if args.watch else {}
        return run(args, config)
    except (db.DbError, psycopg.Error, OSError, ValueError) as e:
        return _error(str(e).strip())


def run(
    args: argparse.Namespace,
    config: Mapping[bytes, Sequence[WatchSet]] | None = None,
) -> int:
    """Write the data files ARGS ask for, watching CONFIG's sets (by tree
    root, as watch.load returns them); return the exit status.

    Raises DbError, psycopg.Error, OSError, or ValueError for a bad --tree,
    a CONFIG section that names no single tree, or a watch set with more
    watched dirs than --max-watched.
    """
    conn = db.connect(args.db)
    db.check_schema(conn)
    all_trees = db.trees(conn)
    sets = tree_sets(config or {}, all_trees)
    chosen = select_trees(all_trees, args.tree)
    args.out.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(UTC)
    entries = []
    with conn:
        for tree in chosen:
            start = perf_counter()
            watch_sets = sets.get(tree.id, ())
            try:
                result = read_tree(
                    conn,
                    tree,
                    args.threshold,
                    args.max_units,
                    resolution(args),
                    watch_sets,
                    args.max_watched,
                )
            except TooManyWatched as e:
                raise ValueError(
                    f"tree {tree.id} ({display(tree.fs_path)}): watch set {e.name!r}"
                    f" matches {e.found} directories, more than --max-watched"
                    f" {e.limit}. Narrow its patterns or raise --max-watched."
                ) from None
            if result is None:
                _note(
                    f"tree {tree.id} ({display(tree.fs_path)}) has no samples; skipped"
                )
                continue
            history, errors = result
            path = args.out / f"tree-{tree.id}.json"
            write_json(path, tree_document(tree, history, args.threshold, generated))
            entries.append(index_entry(tree, history, generated))
            print(path)
            if errors:
                _note(
                    f"WARNING: tree {tree.id} ({display(tree.fs_path)}): {errors}"
                    " directories could not be read in its latest sample. Below them,"
                    " changes go unseen and sizes may be stale."
                )
            for warning in watch_warnings(tree, watch_sets, history.watched):
                _note(warning)
            if args.verbose:
                _note(
                    f"tree {tree.id} ({display(tree.fs_path)}):"
                    f" {len(history.times)} points, {len(history.units)} units,"
                    f" {sum(len(w.paths) for w in history.watched)} watched"
                    f" in {_count(len(history.watched), 'set')},"
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
