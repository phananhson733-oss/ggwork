#!/usr/bin/env python3
"""Read one complete Feishu source; only publish its files after validation."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


class FetchError(Exception):
    pass


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def run_cli(args):
    result = subprocess.run(
        ([sys.executable, "-m", "ggwork_pick.source_lark", *args] if os.environ.get("PICK_SOURCE_OWNER_ID") else ["lark-cli", *args]), capture_output=True, text=True, timeout=None if os.environ.get("PICK_SOURCE_OWNER_ID") else 180
    )
    if result.returncode:
        try:
            error = json.loads(result.stderr or result.stdout).get("error", {})
            if str(error.get("code")) in {"40403", "source_access_denied"}:
                raise FetchError("source_access_denied; previous complete source preserved")
        except (ValueError, AttributeError):
            pass
        # Error bodies may contain source rows or private links. Never echo them.
        raise FetchError(
            f"lark-cli exited {result.returncode}; check user authentication and source access"
        )
    return result.stdout


def fetch_sheet(token, sheet, destination, stage):
    page = stage / "sheet.json"
    receipt = json.loads(
        run_cli(
            [
                "sheets",
                "+csv-get",
                "--spreadsheet-token",
                token,
                "--sheet-id",
                sheet,
                "--as",
                "user",
                "--output-path",
                str(page),
            ]
        )
    )
    receipt = receipt.get("data", receipt)
    body = load(page)
    body = body.get("data", body)
    if (
        receipt.get("complete") is not True
        or receipt.get("truncated") is True
        or body.get("has_more") is not False
    ):
        raise FetchError("sheet export is incomplete; previous snapshot preserved")
    os.replace(page, destination)


def fetch_base(token, table, destination, stage):
    offset, pages, seen, first = 0, 0, set(), None
    combined = stage / "complete.ndjson"
    with combined.open("w", encoding="utf-8") as output:
        while True:
            page = stage / "page.ndjson"
            run_cli(
                [
                    "base",
                    "+record-list",
                    "--base-token",
                    token,
                    "--table-id",
                    table,
                    "--as",
                    "user",
                    "--format",
                    "ndjson",
                    "--limit",
                    "2000",
                    "--offset",
                    str(offset),
                    "--output",
                    str(page),
                    "--overwrite",
                ]
            )
            meta = load(page.with_suffix(".manifest.json"))
            rows = [
                json.loads(line)
                for line in page.read_text(encoding="utf-8").splitlines()
                if line.strip()
            ]
            if (
                meta.get("rev") is None
                or not meta.get("table_id")
                or type(meta.get("has_more")) is not bool
            ):
                raise FetchError(
                    "page manifest lacks revision, table or pagination state"
                )
            if meta.get("records_count") != len(rows):
                raise FetchError("page record count does not match its manifest")
            if first is None:
                first = meta
            elif any(
                meta.get(key) != first.get(key)
                for key in ("rev", "table_id", "query_context")
            ):
                raise FetchError(
                    "source changed between pages; previous snapshot preserved"
                )
            for row in rows:
                identity = row.get("record_id")
                if not isinstance(identity, str) or not identity or identity in seen:
                    raise FetchError(
                        "missing or duplicate record_id; previous snapshot preserved"
                    )
                seen.add(identity)
                output.write(json.dumps(row, ensure_ascii=False) + "\n")
            pages += 1
            if not meta["has_more"]:
                break
            next_offset = meta.get("next_offset")
            if not rows or type(next_offset) is not int or next_offset <= offset:
                raise FetchError("invalid next_offset; previous snapshot preserved")
            offset = next_offset
    manifest = destination.with_suffix(".manifest.json")
    complete = {
        **first,
        "complete_table": True,
        "records_count": len(seen),
        "page_count": pages,
        "has_more": False,
        "next_offset": None,
        "record_file": str(destination),
        "record_file_size_bytes": combined.stat().st_size,
        "manifest_file": str(manifest),
        "columns": {
            key: {k: v for k, v in col.items() if k != "stats"}
            for key, col in first.get("columns", {}).items()
        },
    }
    staged_manifest = stage / "complete.manifest.json"
    staged_manifest.write_text(
        json.dumps(complete, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    os.replace(combined, destination)
    os.replace(staged_manifest, manifest)


def read_records(directory, names):
    """New exports already contain every page; legacy split snapshots remain readable."""
    manifest = (directory / names[0]).with_suffix(".manifest.json")
    if manifest.exists() and load(manifest).get("complete_table") is True:
        names = names[:1]
    result = []
    for name in names:
        path = directory / name
        if path.exists():
            result.extend(
                json.loads(line)
                for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()
            )
    return result


def main():
    mode, token, resource, filename = sys.argv[1:]
    destination = Path(filename)
    destination.parent.mkdir(parents=True, exist_ok=True)
    os.umask(0o077)
    try:
        with tempfile.TemporaryDirectory(
            prefix=".fetch-", dir=destination.parent
        ) as temp:
            # lark-cli only accepts output paths relative to its working directory.
            stage = Path(temp).resolve().relative_to(Path.cwd())
            if mode == "sheet":
                fetch_sheet(token, resource, destination, stage)
            elif mode == "base":
                fetch_base(token, resource, destination, stage)
            else:
                raise FetchError("unsupported source kind")
    except FetchError as exc:
        print(f"fetch {destination.name}: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(
            f"fetch {destination.name}: {type(exc).__name__}; previous complete source files remain available",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
