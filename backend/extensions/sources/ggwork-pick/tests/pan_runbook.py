"""The weekly pan check's runbook, docs/pick-workbench/supabase.md section 6, as the tests read and run it.

Shared by test_pan_runbook_sql.py (the two SQL scripts, the host's threads) and test_pan_runbook_disk.py (the container's
disk). Shell commands are taken from the runbook text and run with bash after its PAN= line, a test directory standing in
for the gateway volume at /data.
"""

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import httpx
from test_realshort_sync import RULES, TOKEN

ROOT = Path(__file__).resolve().parents[3]
DOCS = ROOT / "docs/pick-workbench"
CHECK = DOCS / "supabase/pan-check.sql"
REDACT = DOCS / "supabase/pan-redact.sql"
RUNBOOK = DOCS / "supabase.md"
# The shells the runbook's commands may run in: the gateway image sets C.UTF-8, an ssh session may come up in C.
LOCALES = ("C", "C.UTF-8")

CODE_NOTE = "访问码：8k2p，速存"
# Only a password and its separator, split by whitespace that JSON writes as the two characters \n and \t.
PASSWORD_NEWLINE = "密码\n：ab12"
PASSWORD_TAB = "密碼\t= cd34"
# U+000B, which JSON always writes as an escape, and U+00A0, which ensure_ascii escapes.
PASSWORD_VT = "密码" + chr(0x0B) + "：ab12"
PASSWORD_NBSP = "密码" + chr(0xA0) + "：cd34"


def feed_transport(rows, *, scope: str) -> httpx.MockTransport:
    """One feed page with rules, the way RealShort sends it; the scope lands in each batch's validation_json."""
    body = {"ok": True, "version": "pick-feed-v1", "capturedAt": "2026-09-23T03:00:00.000Z", "scope": scope, "total": len(rows)}
    body |= {"freshness": {"catalogImportedAt": "2026-09-22T03:19:21.327Z"}, "rules": RULES, "rows": rows, "nextCursor": None}

    def handler(request: httpx.Request):
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401)
        return httpx.Response(200, json=body)

    return httpx.MockTransport(handler)


async def pull(service, rows, *, scope: str = "test scope", **kwargs) -> dict:
    """A manual RealShort sync of these feed rows, which must succeed."""
    from ggwork_pick.sync import RealShortSync

    transport = feed_transport(rows, scope=scope)
    outcome = await RealShortSync(service, base_url="https://realshort.test", token=TOKEN, transport=transport, **kwargs).run("manual")
    assert outcome["status"] == "success", outcome
    return outcome


def feed_signal(i, **fields):
    return {"kind": "kd", "label": "KalosTV 日榜", "source_ref": f"ref:{i}", "observed_at": "2026-09-20", "rank": i, "grade": "", "note": "", **fields}


def password(space: int) -> str:
    """A password whose only gap is one raw non-ASCII whitespace character."""
    return "密码" + chr(space) + "：ab12"


def pattern_of(text: str) -> str:
    [pattern] = re.findall(r"^SELECT '([^']*)' AS pan \\gset$", text, re.M)
    return pattern


def runbook_lines() -> list[str]:
    # split, not splitlines: U+0085, U+2028 and U+2029 inside the PAN= line are not line breaks.
    return [line.strip() for line in RUNBOOK.read_text(encoding="utf-8").split("\n")]


def runbook_pan() -> str:
    [line] = [line for line in runbook_lines() if line.startswith("PAN='")]
    return line


def runbook_steps() -> dict[int, str]:
    """Section 6's numbered steps of the pan check, by number."""
    text = RUNBOOK.read_text(encoding="utf-8")
    section = text[text.index("**网盘片段核查") : text.index("## 7.")]
    parts = re.split(r"^(\d+)\. \*\*", section, flags=re.M)
    return {int(number): body for number, body in zip(parts[1::2], parts[2::2], strict=True)}


def step_lines(step: int) -> list[str]:
    return [line.strip() for line in runbook_steps()[step].split("\n")]


def bash(home: Path, command: str, *, locale: str | None = None) -> subprocess.CompletedProcess:
    """A runbook command after the runbook's PAN= line, with home standing in for /data."""
    script = runbook_pan() + "\n" + re.sub(r"(?<![\w.-])/data(?![\w.-])", str(home), command)
    env = os.environ | ({"LC_ALL": locale} if locale else {})
    return subprocess.run(["bash", "-c", script], env=env, capture_output=True, encoding="utf-8", timeout=60)


def shell(home: Path, command: str, *, locale: str | None = None) -> list[str]:
    """The words a command prints; grep's "nothing matched" is not a failure."""
    result = bash(home, command, locale=locale)
    assert result.returncode in (0, 1), result.stdout + result.stderr
    return sorted(result.stdout.split())


@dataclass(frozen=True)
class Scan:
    code: int
    feed_files: list[str] | None  # None: the scan gave no list
    threads: str | None  # the line under [线程], what goes into -v disk_threads
    errors: str


def scan_script() -> str:
    """Step 1's pan_scan, defined and called as the runbook writes it."""
    lines = step_lines(1)
    start = lines.index("pan_scan() {")
    [end] = [i for i, line in enumerate(lines) if line.startswith("pan_scan;")]
    return "\n".join(lines[start : end + 1])


def scan(home: Path, *, locale: str | None = None) -> Scan:
    result = bash(home, scan_script(), locale=locale)
    lines = result.stdout.split("\n")
    [code] = [int(line.rsplit(" ", 1)[1]) for line in lines if line.startswith("pan_scan 退出码 ")]
    if "[线程]" not in lines:
        return Scan(code, None, None, result.stderr)
    feed_files = lines[lines.index("[原始 feed 文件]") + 1 : lines.index("[线程]")]
    after = lines[lines.index("[线程]") + 1]
    # BSD paste prints nothing for no threads, GNU paste an empty line.
    threads = "" if after.startswith("pan_scan 退出码 ") else after
    return Scan(code, sorted(feed_files), threads, result.stderr)


def clean_scan(home: Path, *, locale: str | None = None) -> bool:
    return scan(home, locale=locale) == Scan(0, [], "", "")
