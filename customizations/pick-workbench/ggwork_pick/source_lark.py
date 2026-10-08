"""Private collector CLI bridge; model tools cannot request files or arbitrary sources."""

import json
import os
import sys
from pathlib import Path

from ggwork_pick import lark_runner


def request(args: list[str]):
    command = tuple(args[:2])
    allowed = {
        ("sheets", "+csv-get"): {"--spreadsheet-token", "--sheet-id", "--as", "--output-path"},
        ("base", "+record-list"): {"--base-token", "--table-id", "--as", "--format", "--limit", "--offset", "--output"},
    }
    if command not in allowed:
        raise ValueError("Unsupported source command")
    pairs = {}
    rest = args[2:]
    if command == ("base", "+record-list") and rest and rest[-1] == "--overwrite":
        rest = rest[:-1]
    if len(rest) % 2:
        raise ValueError("Invalid source arguments")
    for key, value in zip(rest[::2], rest[1::2], strict=True):
        if key not in allowed[command] or key in pairs:
            raise ValueError("Invalid source arguments")
        pairs[key] = value
    if set(pairs) != allowed[command] or pairs["--as"] != "user":
        raise ValueError("Invalid source identity or arguments")
    if command[0] == "sheets":
        return "sheet", pairs["--spreadsheet-token"], pairs["--sheet-id"], 0, pairs["--output-path"]
    if pairs["--format"] != "ndjson" or pairs["--limit"] != "2000":
        raise ValueError("Invalid export shape")
    return "base", pairs["--base-token"], pairs["--table-id"], int(pairs["--offset"]), pairs["--output"]


def main(args: list[str]) -> int:
    kind, token, table, offset, destination = request(args)
    owner = os.environ.get("PICK_SOURCE_OWNER_ID", "")
    root = Path(os.environ["PICK_SOURCE_WORKDIR"]).resolve(strict=True)
    path = Path(destination).resolve()
    if not path.is_relative_to(root) or path.is_symlink():
        raise ValueError("Source output is outside its private work directory")
    result = lark_runner.run_catalog_export(owner, kind, token, table, offset)
    if result.completed.exit_code or result.completed.truncated:
        # Do not send the CLI's error body (which may contain private source values) to logs.
        code = "source_read_failed"
        try:
            error = json.loads(result.completed.stderr or result.completed.stdout).get("error", {})
            if str(error.get("code")) == "40403":
                code = "source_access_denied"
        except (ValueError, AttributeError):
            pass
        print(json.dumps({"ok": False, "error": {"type": "source_read_failed", "code": code}}))
        return 1
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_text(result.files["source.json" if kind == "sheet" else "source.ndjson"], encoding="utf-8")
    path.chmod(0o600)
    if kind == "base":
        manifest = path.with_suffix(".manifest.json")
        manifest.write_text(result.files["source.manifest.json"], encoding="utf-8")
        manifest.chmod(0o600)
    print(result.completed.stdout)
    return 0


if __name__ == "__main__":
    import signal

    def terminate(_signum, _frame):
        raise SystemExit(143)

    signal.signal(signal.SIGTERM, terminate)
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (ValueError, KeyError, OSError, lark_runner.LarkUnavailable, TimeoutError):
        print('{"ok":false,"error":{"type":"source_read_failed"}}')
        raise SystemExit(1) from None
