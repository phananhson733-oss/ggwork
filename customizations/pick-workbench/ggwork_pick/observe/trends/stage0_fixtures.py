"""Redacted real answers from stage 0, in the shape of TR-02's constructed fixtures (plan TR-05 deliverable).

The runner keeps every API answer under raw/day<N>/. This turns them into fixture files (constructed: false) under
fixtures/day<N>/ in the artifacts root, for a person to read before any of them replaces a constructed fixture in
tests/fixtures/trends. Nothing here writes into the repository.

What is taken out: every control term (as typed, and URL-encoded) becomes a synthetic placeholder; a related query and
its link become a stable synthetic name; widget tokens become REDACTED_TOKEN; anything shaped like an IP address (a
consent or "sorry" page can print the caller's) becomes a documentation address. Values, flags, times and the answer's
shape stay as they came.

The raw archive keeps no response header (a Location can quote the term, a Set-Cookie is a secret), so the two headers a
replay needs are rebuilt: Location from the recorded redirect kind and host (sorry page, consent wall, elsewhere on
Trends), without any query string; content-type from the body (HTML, JSON after the XSSI prefix, or plain text). A
replayed fixture then earns the status the real answer did (TR-02's classify reads both).
"""

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from functools import reduce
from pathlib import Path
from types import MappingProxyType
from urllib.parse import quote, quote_plus

from ggwork_pick.observe.trends.parse import BASE_HOST, XSSI_PREFIX, strip_xssi
from ggwork_pick.observe.trends.source import RedirectKind
from ggwork_pick.observe.trends.stage0 import Controls
from ggwork_pick.observe.trends.stage0_run import Stage0Paths, load_results, read_jsonl, write_private

REDACTED_TOKEN = "REDACTED_TOKEN"
HTML, JSON, TEXT = "text/html; charset=UTF-8", "application/json; charset=utf-8", "text/plain; charset=utf-8"
LOCATIONS = MappingProxyType(
    {
        RedirectKind.SORRY.value: "https://{host}/sorry/index",
        RedirectKind.CONSENT.value: "https://{host}/",
        RedirectKind.SAME_HOST.value: "https://{host}/trends/",
        RedirectKind.OTHER.value: "https://{host}/",
    }
)
DOCUMENTATION_IP = "192.0.2.1"  # RFC 5737
IP_SHAPED = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])|(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{1,4}:){3,7}[0-9A-Fa-f]{1,4}(?![0-9A-Fa-f:])")
Table = tuple[tuple[re.Pattern, str], ...]


def placeholders(controls: Controls) -> Mapping[str, str]:
    """Control id -> the synthetic term that stands in for it."""
    return MappingProxyType({control.id: f"stage0 {control.group} {k}" for k, control in enumerate(controls.controls, 1)})


def term_table(controls: Controls) -> Table:
    """Every spelling of every control term, longest first, with its placeholder."""
    names = placeholders(controls)
    pairs = {(spelling, names[c.id]) for c in controls.controls for spelling in (c.term, quote_plus(c.term), quote(c.term))}
    ordered = sorted(pairs, key=lambda pair: -len(pair[0]))
    return tuple((re.compile(re.escape(spelling), re.IGNORECASE), fake) for spelling, fake in ordered)


def scrub_text(text: str, table: Table) -> str:
    replaced = reduce(lambda acc, pair: pair[0].sub(pair[1], acc), table, text)
    return IP_SHAPED.sub(DOCUMENTATION_IP, replaced)


def related_name(query: str) -> str:
    return f"related query {hashlib.sha256(query.casefold().encode('utf-8')).hexdigest()[:6]}"


def _scrub_mapping(value: Mapping, table: Table) -> dict:
    scrubbed = {key: REDACTED_TOKEN if key == "token" and isinstance(item, str) else scrub(item, table) for key, item in value.items()}
    if not isinstance(value.get("query"), str):
        return scrubbed
    name = related_name(value["query"])
    link = {"link": f"/trends/explore?q={quote_plus(name)}"} if "link" in value else {}
    return {**scrubbed, "query": name, **link}


def scrub(value: object, table: Table) -> object:
    """A new copy of a decoded answer with the terms, related queries, tokens and addresses taken out."""
    if isinstance(value, Mapping):
        return _scrub_mapping(value, table)
    if isinstance(value, list):
        return [scrub(item, table) for item in value]
    if isinstance(value, str):
        return scrub_text(value, table)
    return value


def location_of(entry: Mapping) -> str | None:
    """The redirect's target rebuilt from its recorded kind and host; None when the answer was no redirect."""
    kind, status = entry.get("redirect_kind"), entry.get("http_status")
    if kind not in LOCATIONS or status is None or not 300 <= status < 400:
        return None
    host = entry.get("redirect_host") or BASE_HOST
    return LOCATIONS[kind].format(host=host)


def _headers(entry: Mapping, content_type: str) -> list[list[str]]:
    location = location_of(entry)
    return [["content-type", content_type], *([["location", location]] if location else [])]


def response_of(entry: Mapping, body: bytes, table: Table) -> dict:
    """The fixture's `response`: JSON after the prefix when it decodes, the scrubbed text otherwise; headers rebuilt."""
    status, text = entry["http_status"], body.decode("utf-8", errors="replace")
    stripped = strip_xssi(text)
    try:
        decoded = json.loads(stripped)
    except ValueError:
        kind = HTML if text.lstrip()[:1] == "<" else TEXT
        return {"status": status, "headers": _headers(entry, kind), "text": scrub_text(text, table)}
    prefix = text[: len(text) - len(stripped)] if text.startswith(XSSI_PREFIX) else ""
    return {"status": status, "headers": _headers(entry, JSON), "prefix": prefix, "json": scrub(decoded, table)}


def _fixture(entry: Mapping, line: Mapping | None, controls: Controls, body: bytes, table: Table) -> dict:
    control = controls.by_id().get(line["control"]) if line else None
    name = placeholders(controls).get(control.id) if control else None
    query = {"terms": [name], "bare": name, "geo": control.geo, "granularity": line["granularity"]} if control else None
    return {
        "about": f"Stage 0, redacted real answer: {entry['phase']} for a {control.group if control else 'unknown'} control.",
        "constructed": False,
        "pending_stage0": [],
        "phase": entry["phase"],
        **({"query": query} if query else {}),
        "response": response_of(entry, body, table),
    }


def write_fixtures(paths: Stage0Paths, day: int, controls: Controls) -> Sequence[Path]:
    """One fixture per captured API answer of `day`, under fixtures/day<N>/ (files 600). Returns the files written."""
    raw, table = paths.raw_dir(day), term_table(controls)
    lines = load_results(paths.run_dir(day))
    written = []
    for entry in read_jsonl(raw / "index.jsonl"):
        if not entry.get("body_file") or entry.get("http_status") is None:
            continue
        body = (raw / entry["body_file"]).read_bytes()
        target = paths.root / "fixtures" / f"day{day}" / f"stage0_day{day}_{entry['seq']:04d}_{entry['phase']}.json"
        fixture = _fixture(entry, lines.get(entry.get("unit")), controls, body, table)
        write_private(target, json.dumps(fixture, ensure_ascii=False, indent=2) + "\n")
        written.append(target)
    return tuple(written)
