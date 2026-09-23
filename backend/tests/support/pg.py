"""Throwaway PostgreSQL databases on the cluster named by PICK_TEST_PG_URL (libpq URL, superuser).

Cases that need one skip when PICK_TEST_PG_URL is unset. Once it is set, a missing driver fails them instead of
skipping, like the extension's fixtures (customizations/pick-workbench/tests/pg.py): a run meant to cover
PostgreSQL must not pass without it.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from uuid import uuid4

URL_ENV = "PICK_TEST_PG_URL"


def cluster_url() -> str | None:
    return os.environ.get(URL_ENV, "").strip() or None


@contextmanager
def fresh_database(url: str, prefix: str) -> Iterator[str]:
    """A new database on the cluster at url, dropped afterwards; yields its libpq URL."""
    import psycopg
    from psycopg import sql
    from sqlalchemy.engine import make_url

    name = f"{prefix}_{uuid4().hex[:12]}"
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield make_url(url).set(database=name).render_as_string(hide_password=False)
    finally:
        with psycopg.connect(url, autocommit=True) as conn:
            conn.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))
