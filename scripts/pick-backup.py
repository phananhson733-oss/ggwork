#!/usr/bin/env python3
"""Copy an offline DeerFlow home, preserving SQLite and original pick sources.

Stop the Gateway using this home before running. The destination must not exist.
Restore by copying the verified directory back to its original home while stopped.
Configuration files outside the home must be backed up separately and kept private.
"""

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
from pathlib import Path


def verify(home):
    database = home / "data" / "deerflow.db"
    with sqlite3.connect(f"file:{database}?mode=ro", uri=True) as connection:
        if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("SQLite integrity check failed")
        for digest, raw_path in connection.execute(
            "SELECT content_hash, raw_blob_path FROM ggwp_import_batches"
        ):
            # Original source paths are absolute. Resolve the immutable stored
            # basename against this snapshot, not against a live source folder.
            original = Path(raw_path)
            candidate = home / "pick" / original.parent.name / original.name
            if hashlib.sha256(candidate.read_bytes()).hexdigest() != digest:
                raise RuntimeError("Import source checksum mismatch")
        return {
            table: connection.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            for table in (
                "ggwp_import_batches",
                "ggwp_candidate_sets",
                "ggwp_selections",
                "ggwp_selection_commands",
            )
        }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    parser.add_argument("--gateway-stopped", action="store_true", required=True)
    args = parser.parse_args()
    source, destination = args.source.resolve(), args.destination.resolve()
    if destination.exists() or destination.is_relative_to(source):
        parser.error("Destination must be new and outside the source")
    before = verify(source)
    previous = os.umask(0o077)
    try:
        shutil.copytree(source, destination, symlinks=True)
        after = verify(destination)
        if after != before:
            raise RuntimeError("Snapshot counts changed; verify the Gateway is stopped")
        (destination / "pick-backup-receipt.json").write_text(
            json.dumps({"original_home": str(source), "counts": after}, indent=2)
        )
        print(json.dumps({"verified": True, "counts": after}))
    finally:
        os.umask(previous)


if __name__ == "__main__":
    main()
