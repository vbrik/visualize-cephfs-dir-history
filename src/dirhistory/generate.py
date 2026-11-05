"""dirhistory-generate: write the website's data files from a trackdir database."""

import argparse
import json
import os
import re
import sys
import tempfile
from collections.abc import Collection, Mapping, Sequence
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
    Resolution,
    Series,
    TreeHistory,
    summarize,
)
from dirhistory.sets import (
    IMPLICIT,
    DirSet,
    SetDirs,
    TreeConfig,
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

The sets file (--sets, required) names the trees to generate and, per tree,
sets of directories and a budget: the most directories its file stores.
Each tree also gets the implicit set "Top level", its top-level
directories. The page's Stack view charts the rbytes (or, toggled, the
rentries: rfiles + rsubdirs) of a set's directories as bands, and can split
a directory into its subdirectories, down to the depth the set stores. Its
Trends view shows one card per directory of a set.

A tree whose sets cover more directories than its budget is stored
breadth-first, each level from the tree root whole until the budget runs
out, and saved partial with a warning; the page marks a directory whose
subdirectories were left out with *. Raise the budget to store more.

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


def select_trees(
    all_trees: Sequence[Tree], configured: Collection[int], ids: Sequence[int]
) -> list[Tree]:
    """Return the trees of ALL_TREES with IDS, or all CONFIGURED ones (ids of
    trees with a section in the sets file) if IDS is empty.

    Raises ValueError naming any id without a tree, else the first id of a
    tree that is not configured.
    """
    if not ids:
        return [t for t in all_trees if t.id in configured]
    if unknown := sorted(set(ids) - {t.id for t in all_trees}):
        raise ValueError(f"no tree with id {', '.join(map(str, unknown))}")
    if bare := [t for t in all_trees if t.id in ids and t.id not in configured]:
        tree = bare[0]
        raise ValueError(
            f"tree {tree.id} ({display(tree.fs_path)}) has no section in --sets"
        )
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
        "pruned": [os.fsdecode(p) for p in history.pruned],
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
    config: TreeConfig,
) -> tuple[TreeHistory, int] | None:
    """Return a tree's history and how many of its directories are in error
    as of its latest sample, or None if it has no samples. Reads in one
    transaction, i.e. one snapshot. CONFIG's sets and budget go to
    summarize."""
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
            config.sets,
            config.budget,
        )
    return history, errors


def set_warnings(
    tree: Tree, dir_sets: Sequence[DirSet], found: Sequence[SetDirs]
) -> list[str]:
    """Return, per set of TREE's DIR_SETS, a warning per pattern that matched
    no directory, then one if the budget left out some of its dirs (FOUND,
    per set, counts both). The implicit set never warns of a pattern: it is
    no one's mistake if a tree has no subdirectories."""
    head = f"WARNING: tree {tree.id} ({display(tree.fs_path)}): set"
    warnings = []
    for s, f in zip(dir_sets, found, strict=True):
        if s is not IMPLICIT:
            warnings += [
                f"{head} {s.name!r}: pattern {p.text!r} matches no directory"
                for (p, _), n in zip(s.patterns(), f.found, strict=True)
                if not n
            ]
        if f.dropped:
            warnings.append(
                f"{head} {s.name!r}: {f.dropped} of its {len(f.paths) + f.dropped}"
                " dirs are below the budget's cut and left out"
            )
    return warnings


def cut_warning(tree: Tree, history: TreeHistory, budget: int) -> str | None:
    """Return the warning that BUDGET cut TREE's HISTORY, or None if it did
    not."""
    if history.cut_level is None:
        return None
    n = len(history.pruned)
    pruned = (
        f"; {n} director{'y' if n == 1 else 'ies'} whose subdirectories were left"
        f" out {'is' if n == 1 else 'are'} marked * in the page"
        if n
        else ""
    )
    return (
        f"WARNING: tree {tree.id} ({display(tree.fs_path)}): budget {budget}"
        f" reached: stored {len(history.rbytes)} of {history.candidates}"
        f" directories, every level above depth {history.cut_level} in full"
        f"{pruned}. Raise the tree's budget in the sets file to store more."
    )


def configured(
    config: Mapping[bytes, TreeConfig], all_trees: Sequence[Tree]
) -> dict[int, TreeConfig]:
    """Return the config of each configured tree by id: each section of
    CONFIG (by tree root) goes to the tree of ALL_TREES with that fs_path,
    with the implicit set added (sets.with_implicit). Trees without a section
    are left out.

    Raises ValueError for a section without a tree, or with several trees
    (tree roots are assumed unique, so they cannot be told apart).
    """
    by_tree: dict[int, TreeConfig] = {}
    for root, tree in config.items():
        ids = [t.id for t in all_trees if t.fs_path == root]
        if not ids:
            raise ValueError(f"--sets: no tree has root {display(root)}")
        if len(ids) > 1:
            raise ValueError(
                f"--sets: trees {', '.join(map(str, ids))} all have root"
                f" {display(root)}, so it cannot name one"
            )
        by_tree[ids[0]] = TreeConfig(tree.budget, with_implicit(tree.sets))
    return by_tree


def merged_index(out: Path, entries: list[dict], live: Collection[int]) -> list:
    """Return trees.json's new entries: ENTRIES, plus those of the existing
    index for trees not in ENTRIES that are LIVE (still exist and are
    configured), by tree id."""
    try:
        old = json.loads((out / INDEX).read_text(encoding="ascii"))
    except FileNotFoundError:
        old = []
    fresh = {e["id"] for e in entries}
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
        help="TOML file of the trees to generate: per tree, named sets of"
        ' directories, [tree."/tree/root".set."NAME"] dirs = [{ match = ["dir/*"],'
        " depth = N }], and a budget, the most directories its file stores"
        " (budget = N at the top for every tree, or in a tree's"
        ' [tree."/tree/root"] section); see deploy/sets.toml.example',
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
        help="only this tree; repeatable (default: every tree in --sets)",
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


def run(args: argparse.Namespace, config: Mapping[bytes, TreeConfig]) -> int:
    """Write the data files ARGS ask for, of CONFIG's trees (by tree root,
    as sets.load returns them); return the exit status.

    Raises DbError, psycopg.Error, OSError, or ValueError for a bad --tree
    or a CONFIG section that names no single tree.
    """
    conn = db.connect(args.db)
    db.check_schema(conn)
    all_trees = db.trees(conn)
    configs = configured(config, all_trees)
    chosen = select_trees(all_trees, configs, args.tree)
    if args.verbose and (n := len(all_trees) - len(configs)):
        _note(f"{_count(n, 'tree')} without a section in --sets skipped")
    args.out.mkdir(parents=True, exist_ok=True)
    generated = datetime.now(UTC)
    entries = []
    with conn:
        for tree in chosen:
            start = perf_counter()
            tree_config = configs[tree.id]
            result = read_tree(conn, tree, resolution(args), tree_config)
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
            if warning := cut_warning(tree, history, tree_config.budget):
                _note(warning)
            for warning in set_warnings(tree, tree_config.sets, history.sets):
                _note(warning)
            if args.verbose:
                _note(
                    f"tree {tree.id} ({display(tree.fs_path)}):"
                    f" {len(history.times)} points, {len(history.rbytes)} stored"
                    f" dirs in {_count(len(history.sets), 'set')},"
                    f" {len(history.pruned)} pruned, {perf_counter() - start:.1f} s"
                )
    index = args.out / INDEX
    write_json(index, merged_index(args.out, entries, configs))
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
