"""Read immutable private snapshots; never create, migrate or write a database."""

import sqlite3
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def get_db_connection(path: Path):
    path = Path(path).resolve(strict=True)
    # immutable is safe only for this sealed input; reject any nonempty WAL first.
    wal = Path(str(path) + "-wal")
    if wal.exists() and wal.stat().st_size:
        raise ValueError("nonempty WAL: obtain a consistent SQLite backup first")
    connection = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
    finally:
        connection.close()
