"""Pan-link scrub of RealShort feed v2, ported for the workbench's text gates (plan 4.6, 4.8).

RealShort's scrubPanText (src/lib/pick/export-v2-map.ts:602-639 at 816ca2e) and this module follow one shared fixture:
pan_rules.json, a byte copy of RealShort's tests/fixtures/pan-scrub-cases.json (tests/fixtures/SOURCE.json records the
commit and sha256). Every pattern and rule is read from it once, at import; none is restated here. The semantics are the
fixture's: a string in which any pan fragment is recognised is replaced whole and counts one hit, otherwise it is kept.

Scanning walks leaves only, like scrubAllText (:653-673): each string is scrubbed on its own, never a dict or list joined
into text (jsonb::text would make up hits across leaves). A row's top-level keys are exempt by name (SCRUB_EXEMPT_KEYS),
nested keys only by path (SCRUB_EXEMPT_PATHS); array indexes are written [*]. Everything is synchronous and CPU-bound
(re and unicodedata hold the GIL): callers run it in asyncio.to_thread.
"""

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from functools import partial, reduce
from importlib import resources
from operator import or_
from types import MappingProxyType

from ggwork_pick.mirror.contracts import ODD_KEY, PATH_PART_MAX, ROW_RESOURCES

RULES_FILE = "pan_rules.json"
STEPS = ("entities", "form", "dots", "strip", "markup", "tags", "percent")
COPIES = ("delete", "keep", "value", "keyless")
# The only python_flags spellings the fixture uses; anything else is a rule change this port has not followed.
_FLAGS = MappingProxyType({"0": 0, "re.IGNORECASE": re.IGNORECASE, "re.ASCII": re.ASCII})
_FORMS = frozenset({"NFC", "NFD", "NFKC", "NFKD"})

# export-v2-map.ts:213-222 (IDENTITY_KEYS, DATE_KEYS): ids, links and dates, exempt at a row's top level by name.
IDENTITY_KEYS = (
    "row_key", "sd", "feishu_record", "id", "drama_id", "canonical_id", "book_id", "slug", "title_key",
    "in_site_ids", "row_keys", "drama_ids", "source_id", "detail_url", "source_ref",
)  # fmt: skip
DATE_KEYS = (
    "listed_on", "off_on", "latest_evidence_on", "imported_at", "evidence_on", "online_on", "last_post_on", "created_on",
    "updated_on", "first_post_on", "metric_at", "as_of", "rs_clk_on", "rs_bill_on", "rs_gsc_on", "publish_at", "synced_at",
    "search_data_at", "detail_synced_at", "baseline1_at", "baseline7_at", "baseline15_at", "last_click_on", "last_bill_on",
    "day", "bill_date", "listed_at", "observed_at",
)  # fmt: skip
SCRUB_EXEMPT_KEYS = frozenset(IDENTITY_KEYS + DATE_KEYS)
# export-v2-map.ts:230: nested exemptions by path from a row's root; only v1 rows have them.
SCRUB_EXEMPT_PATHS = frozenset({"signals[*].source_ref", "signals[*].observed_at", "posted.last_post_on"})
# A key written into a hit path as it is: the name shape meta.scrub's paths take (contracts.SCRUB_PATH). Any other key
# is written ODD_KEY, as contracts' error paths do: paths get printed (--scan), and a key can hold the very text scanned for.
_PLAIN_KEY = re.compile(rf"[A-Za-z0-9_]{{1,{PATH_PART_MAX}}}")


@dataclass(frozen=True, slots=True)
class PanRules:
    """The fixture, compiled. Patterns compile with the fixture's python_flags; detect copies take its pattern's flags."""

    replacement: str
    url: re.Pattern
    code: re.Pattern
    detect_url: re.Pattern
    detect_code: re.Pattern
    order: tuple[str, ...]
    entities: re.Pattern
    entity_names: MappingProxyType
    entity_max_dec: int
    entity_max_hex: int
    form: str
    dots: re.Pattern
    strip: re.Pattern
    markup: re.Pattern
    tags: re.Pattern
    tag_value: re.Pattern
    tag_keyword: re.Pattern
    tag_keyword_mask: str
    percent: re.Pattern
    printable: tuple[int, int]
    probe_passes: int


def _flags(text: str) -> int:
    names = [part.strip() for part in text.split("|")]
    unknown = [name for name in names if name not in _FLAGS]
    if unknown:
        raise ValueError(f"{RULES_FILE}：python_flags 里有本移植不认识的写法 {unknown}")
    return reduce(or_, (_FLAGS[name] for name in names), 0)


def _check_semantics(fixture: dict) -> None:
    """Refuse a fixture whose flow differs from the one ported here (fail at import, not with a silent mismatch)."""
    normalize, limits = fixture["normalize"], fixture["limits"]
    problems = [
        (sorted(normalize["order"]) != sorted(STEPS), "normalize.order 的步骤"),
        (list(normalize["copies"]) != list(COPIES), "normalize.copies"),
        (normalize["repeat"] is not True, "normalize.repeat"),
        (normalize["form"] not in _FORMS, "normalize.form"),
        (normalize["on_hit_hits"] != 1 or limits["on_limit_hits"] != 1, "命中计数（整串替换记 1）"),
    ]
    changed = [what for failed, what in problems if failed]
    if changed:
        raise ValueError(f"{RULES_FILE} 的规则与本移植不一致：{', '.join(changed)}")


def load_rules(text: str) -> PanRules:
    """Compile the fixture's rules; ValueError when they are not the flow this module implements."""
    fixture = json.loads(text)
    _check_semantics(fixture)
    patterns, normalize, entities = fixture["patterns"], fixture["normalize"], fixture["normalize"]["entities"]
    url_flags, code_flags = _flags(patterns["url"]["python_flags"]), _flags(patterns["code"]["python_flags"])
    low, high = normalize["printable"]
    return PanRules(
        replacement=fixture["replacement"],
        url=re.compile(patterns["url"]["source"], url_flags),
        code=re.compile(patterns["code"]["source"], code_flags),
        detect_url=re.compile(normalize["detect"]["url"], url_flags),
        detect_code=re.compile(normalize["detect"]["code"], code_flags),
        order=tuple(normalize["order"]),
        entities=re.compile(entities["pattern"]),
        entity_names=MappingProxyType(dict(entities["names"])),
        entity_max_dec=entities["max_digits"]["dec"],
        entity_max_hex=entities["max_digits"]["hex"],
        form=normalize["form"],
        dots=re.compile(normalize["dots"]),
        strip=re.compile(normalize["strip"]),
        markup=re.compile(normalize["markup"]),
        tags=re.compile(normalize["tags"]),
        tag_value=re.compile(normalize["tag_value"]),
        tag_keyword=re.compile(normalize["tag_keyword"]),
        tag_keyword_mask=normalize["tag_keyword_mask"],
        percent=re.compile(normalize["percent"]),
        printable=(low, high),
        probe_passes=fixture["limits"]["probe_passes"],
    )


_RULES_BYTES = resources.files(__package__).joinpath(RULES_FILE).read_bytes()
RULES_SHA256 = hashlib.sha256(_RULES_BYTES).hexdigest()
RULES = load_rules(_RULES_BYTES.decode("utf-8"))


def _entity(rules: PanRules, match: re.Match) -> str:
    """One HTML entity: a known name, or a decimal or hex code point that is a scalar value; anything else as it was."""
    if match[3] is not None:
        return rules.entity_names.get(match[3], match[0])
    decimal = match[1] is not None
    digits = (match[1] if decimal else match[2]).lstrip("0")
    if len(digits) > (rules.entity_max_dec if decimal else rules.entity_max_hex):
        return match[0]
    point = int(digits, 10 if decimal else 16) if digits else 0
    return chr(point) if 1 <= point <= 0x10FFFF and not 0xD800 <= point <= 0xDFFF else match[0]


def _percent(rules: PanRules, match: re.Match) -> str:
    code = int(match[1], 16)
    return chr(code) if rules.printable[0] <= code <= rules.printable[1] else match[0]


def _tag_value(rules: PanRules, match: re.Match) -> str:
    """Copy "value": a tag the value branch matches from its < becomes its value with a space on each side; others go."""
    found = rules.tag_value.match(match[0])
    return f" {found[1]} " if found else ""


def _mask_keywords(rules: PanRules, match: re.Match) -> str:
    """Copy "keyless": the tag stays, the characters of the six keywords in it become the mask."""
    return rules.tag_keyword.sub(rules.tag_keyword_mask, match[0])


Step = Callable[[str], str]


def _steps(rules: PanRules) -> MappingProxyType:
    return MappingProxyType(
        {
            "entities": partial(rules.entities.sub, partial(_entity, rules)),
            "form": partial(unicodedata.normalize, rules.form),
            "dots": partial(rules.dots.sub, "."),
            "strip": partial(rules.strip.sub, ""),
            "markup": partial(rules.markup.sub, ""),
            "tags": partial(rules.tags.sub, ""),
            "percent": partial(rules.percent.sub, partial(_percent, rules)),
        }
    )


def _copy_steps(order: tuple[str, ...], steps, tags: Step | None) -> tuple[tuple[str, Step], ...]:
    """One copy's steps in fixture order; no tags step means the copy skips it (copy "keep")."""
    return tuple((name, tags if name == "tags" else steps[name]) for name in order if name != "tags" or tags is not None)


def _copies(rules: PanRules) -> tuple[tuple[tuple[str, Step], ...], ...]:
    """The four normalized copies in fixture order; they differ only in what removing tags does."""
    steps = _steps(rules)
    tags_as = {
        "delete": steps["tags"],
        "value": partial(rules.tags.sub, partial(_tag_value, rules)),
        "keyless": partial(rules.tags.sub, partial(_mask_keywords, rules)),
    }
    return tuple(_copy_steps(rules.order, steps, tags_as.get(copy)) for copy in COPIES)


_DELETE, *_LATER = _copies(RULES)
_LATER_COPIES = tuple(_LATER)


def _detects(text: str) -> bool:
    return RULES.detect_url.search(text) is not None or RULES.detect_code.search(text) is not None


def _one_pass(text: str, original: str, steps) -> tuple[str, bool, bool]:
    """One pass of a copy: (result, detected, tags changed). Checked after every step that changes the text."""
    tags_changed = False
    for name, step in steps:
        changed = step(text)
        if changed != text:
            tags_changed = tags_changed or name == "tags"
            if changed != original and _detects(changed):
                return changed, True, tags_changed
        text = changed
    return text, False, tags_changed


def _probe(original: str, steps) -> tuple[bool, bool]:
    """(detected, tags changed) for one copy, repeated until it stops changing; passing the pass limit counts as detected."""
    text, tags_changed = original, False
    for _ in range(RULES.probe_passes + 1):
        changed, detected, tags = _one_pass(text, original, steps)
        tags_changed = tags_changed or tags
        if detected:
            return True, tags_changed
        if changed == text:
            return False, tags_changed
        text = changed
    return True, tags_changed


def _recognised(text: str) -> bool:
    if RULES.url.search(text) is not None or RULES.code.search(text) is not None:
        return True
    detected, tags_changed = _probe(text, _DELETE)
    # When removing tags never changed the delete copy, the other three copies go step for step the same way.
    return detected or (tags_changed and any(_probe(text, steps)[0] for steps in _LATER_COPIES))


def scrub_text(text: str) -> tuple[str, int]:
    """(the replacement, 1) when text holds a pan link or extraction code, else (text, 0): RealShort's scrubPanText."""
    if not isinstance(text, str):
        raise TypeError("scrub_text 只收字符串")
    return (RULES.replacement, 1) if _recognised(text) else (text, 0)


def _shown(key: str) -> str:
    return key if _PLAIN_KEY.fullmatch(key) else ODD_KEY


def _leaf_hits(value, at: str, rel: str) -> Iterator[tuple[str, int]]:
    """(path, hits) for every string leaf that scrubs; at is the counted path (keys as _shown writes them), rel the path
    from the row root with keys as they are, for the exemptions."""
    if isinstance(value, str):
        hits = scrub_text(value)[1]
        if hits:
            yield at, hits
    elif isinstance(value, list | tuple):
        for item in value:
            yield from _leaf_hits(item, f"{at}[*]", f"{rel}[*]")
    elif isinstance(value, dict):
        for key, item in value.items():
            here = f"{rel}.{key}" if rel else key
            exempt = here in SCRUB_EXEMPT_PATHS if rel else key in SCRUB_EXEMPT_KEYS
            if not exempt:
                yield from _leaf_hits(item, f"{at}.{_shown(key)}" if at else _shown(key), here)


def scan_value(value, prefix: str = "") -> dict[str, int]:
    """Hits by path over value's string leaves, as scrubAllText(value, prefix) counts them; value is not touched.

    A key that is not a plain name is written ODD_KEY: RealShort would write it as it is, but such a meta.scrub path
    fails the contract anyway (SCRUB_PATH), so the paths only differ where they could never be compared.
    """
    totals: dict[str, int] = {}
    for path, hits in _leaf_hits(value, prefix, ""):
        totals[path] = totals.get(path, 0) + hits
    return totals


def scan_row(resource: str, row: dict) -> dict[str, int]:
    """A v2 row: paths like meta.scrub's, e.g. catalog_signals.payload.h[*][*] (toExportRow's hits)."""
    if resource not in ROW_RESOURCES:
        raise ValueError(f"scan_row 只扫行资源，没有 {str(resource)[:64]}")
    return scan_value(row, resource)


def scan_v1_row(row: dict) -> dict[str, int]:
    """A v1 feed row as toFeedRow scrubs it (feed-map.ts:328): paths from the row root, e.g. signals[*].note."""
    return scan_value(row)


def scan_manifest_meta(meta: dict) -> dict[str, int]:
    """manifest.meta as finalizeManifest scrubs it (export-v2-map.ts:922-928): every key but scrub, under manifest.meta."""
    return scan_value({key: item for key, item in meta.items() if key != "scrub"}, "manifest.meta")


def add_hits(first: dict[str, int], second: dict[str, int]) -> dict[str, int]:
    """Two hit counts added up by path, as a new dict (addScrubCounts, export-v2-map.ts:644-646)."""
    return {**first, **{path: first.get(path, 0) + hits for path, hits in second.items()}}


def scan_v1_page(page: dict) -> dict[str, int]:
    """A feed v1 page: rows[*] by v1's exemptions, under v1.rows[*]; the first page's rules Markdown apart, as v1.rules.

    RealShort sends the rules unscrubbed (feed.ts:63), so they are counted on their own (U45); other page fields
    (scope, freshness) are not scanned, as in G2.
    """
    totals: dict[str, int] = {}
    for row in page.get("rows") or ():
        found = {f"v1.rows[*].{path}" if path else "v1.rows[*]": hits for path, hits in scan_v1_row(row).items()}
        totals = add_hits(totals, found)
    rules = page.get("rules")
    return totals if rules is None else add_hits(totals, scan_value(rules, "v1.rules"))


def scan_v2_page(resource: str, page: dict) -> dict[str, int]:
    """A feed v2 row page: every row as scan_row counts it, added up; paths as meta.scrub writes them."""
    totals: dict[str, int] = {}
    for row in page.get("rows") or ():
        totals = add_hits(totals, scan_row(resource, row))
    return totals
