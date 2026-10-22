"""Read trackdir's PostgreSQL schema.

The contract is trackdir's docs/schema.md: a reader checks the schema version
first, since versions are not migrated and may change any column.
"""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass

import psycopg

from dirhistory.history import Row, Sample

SCHEMA_VERSION = 4  # the trackdir schema version this module reads

# Rows per round trip when streaming a first sample (up to millions of rows)
STREAM_ROWS = 50_000


class DbError(Exception):
    """The database cannot be read as a trackdir schema."""


@dataclass(frozen=True, slots=True)
class Tree:
    """A trackdir tree: a CephFS directory sampled to MAX_DEPTH.

    fs_name None means the cluster's default filesystem.
    """

    id: int
    cluster_fsid: str
    fs_name: str | None
    fs_path: bytes
    max_depth: int


def connect(dsn: str) -> psycopg.Connection:
    """Return a read-only connection whose transactions see one snapshot.

    Each tree is read in one transaction, so a sample stored meanwhile is
    either wholly visible or not at all. Raises DbError if connecting fails.
    """
    try:
        conn = psycopg.connect(dsn, autocommit=True)
    except psycopg.Error as e:
        raise DbError(str(e)) from e
    conn.isolation_level = psycopg.IsolationLevel.REPEATABLE_READ
    conn.read_only = True
    return conn


def check_schema(conn: psycopg.Connection) -> None:
    """Raise DbError unless the trackdir schema is at SCHEMA_VERSION."""
    with conn.transaction():
        if conn.execute("SELECT to_regclass('trackdir.meta')").fetchone()[0] is None:
            raise DbError("no trackdir schema: run `trackdir db init` and sample first")
        found = [v for (v,) in conn.execute("SELECT schema_version FROM trackdir.meta")]
    if found != [SCHEMA_VERSION]:
        raise DbError(
            f"trackdir schema is at version {found or 'none'}, "
            f"this dirhistory reads version {SCHEMA_VERSION}"
        )


def trees(conn: psycopg.Connection) -> list[Tree]:
    """Return every tree, by id."""
    with conn.transaction():
        rows = conn.execute(
            "SELECT id, cluster_fsid, fs_name, fs_path, max_depth"
            " FROM trackdir.tree ORDER BY id"
        ).fetchall()
    return [Tree(*row) for row in rows]


def samples(conn: psycopg.Connection, tree_id: int) -> tuple[list[Sample], int]:
    """Return the tree's samples ordered by (started, id), and their total errors."""
    rows = conn.execute(
        "SELECT id, started, errors FROM trackdir.sample"
        " WHERE tree_id = %s ORDER BY started, id",
        (tree_id,),
    ).fetchall()
    return [Sample(i, started) for i, started, _ in rows], sum(e for *_, e in rows)


def changed_paths(conn: psycopg.Connection, later: list[int]) -> set[bytes]:
    """Return the paths with a row in any of the samples LATER."""
    rows = conn.execute(
        "SELECT DISTINCT path FROM trackdir.dir_state WHERE sample_id = ANY(%s)",
        (later,),
    )
    return {bytes(path) for (path,) in rows}


def sample_rows(
    conn: psycopg.Connection, sample_id: int
) -> Iterator[tuple[bytes, int | None]]:
    """Stream (path, rbytes) of every row of a sample.

    Needs an open transaction: it uses a server-side cursor.
    """
    with conn.cursor(name="sample_rows") as cur:
        cur.itersize = STREAM_ROWS
        cur.execute(
            "SELECT path, rbytes FROM trackdir.dir_state WHERE sample_id = %s",
            (sample_id,),
        )
        for path, rbytes in cur:
            yield bytes(path), rbytes


def path_rows(
    conn: psycopg.Connection, tree_id: int, paths: Iterable[bytes]
) -> list[Row]:
    """Return every row of PATHS in the tree, ordered by sample (started, id)."""
    rows = conn.execute(
        "SELECT d.sample_id, d.path, d.rbytes, d.deleted"
        " FROM trackdir.dir_state d JOIN trackdir.sample s ON s.id = d.sample_id"
        " WHERE s.tree_id = %s AND d.path = ANY(%s)"
        " ORDER BY s.started, s.id",
        (tree_id, list(paths)),
    )
    return [(sid, bytes(path), rbytes, deleted) for sid, path, rbytes, deleted in rows]
