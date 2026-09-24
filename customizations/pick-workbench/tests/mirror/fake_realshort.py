"""A small RealShort feed double for the mirror client tests.

Every shape is copied from RealShort feat/pick-export-v2 @ 816ca2e (rs = /Users/wzb/Code/realshort-pick-export-v2):
- status codes and error bodies: rs:src/lib/pick/feed-http.ts:38-98 (v2 bodies carry version, v1 bodies do not);
- v2 query rules, as_of window, cursors, byte cut: rs:src/lib/pick/export-v2-page.ts:15-112, :143-229;
- v2 envelope and manifest body: rs:src/lib/pick/export-v2.ts:497-538;
- resources, columns, keys and page limits: rs:src/lib/pick/export-v2-map.ts:61-191;
- manifest and meta keys: rs:src/lib/pick/export-v2-map.ts:864-909;
- v1 query rules and page: rs:src/lib/pick/feed-map.ts:21-63, :313-392; v1 row: :165-178.
The same respond() serves httpx.MockTransport in process and http.server for the subprocess test.
"""

import base64
import hashlib
import json
import re
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qsl, urlsplit

import httpx

EXPORT_TOKEN = "export-token-for-tests-4f1d"
FEED_TOKEN = "feed-token-for-tests-9c2a"
BYPASS = "bypass-secret-for-tests-7e0b"
# Every text cell and nested string of every row starts with this; no output may ever contain it.
ROW_SENTINEL = "ROWVALUE"
START = datetime(2026, 9, 23, 12, 34, 56, 789000, tzinfo=UTC)

EXPORT_VERSION = "pick-export-v2"  # rs:src/lib/pick/export-v2-map.ts:25
FEED_VERSION = "pick-feed-v1"  # rs:src/lib/pick/feed-map.ts:21
AS_OF_RE = re.compile(r"^([0-9]{4})-([0-9]{2})-([0-9]{2})T([0-9]{2}):([0-9]{2})(?::00(?:\.000)?)?Z$")  # export-v2-page.ts:22
FP_RE = re.compile(r"^[0-9a-f]{64}$")  # export-v2-page.ts:24
AS_OF_MAX_AGE = timedelta(minutes=30)  # export-v2-page.ts:18
SERIES_DAY_SPAN = 93  # export-v2-page.ts:20

# rs:src/lib/pick/export-v2-map.ts:109-114
CATALOG_SHAPED = [
    "row_key:text", "platform:text", "source_table:text", "title:text", "title_cn:text", "lang:text", "kind:text",
    "origin:text", "tags:text", "listed_on:day?", "episodes:int?", "pay_start:int?", "youtube:bool", "merged_rows:int",
    "off_on:day?", "reoff_note:text", "title_key:text", "in_site_ids:text[]", "legacy_only:bool", "site_other:bool",
    "has_signal:bool", "latest_evidence_on:day?",
]  # fmt: skip
# rs:src/lib/pick/export-v2-map.ts:130-191 (key, maxLimit, columns)
SPECS = {
    "catalog_rows": (["row_key"], 5000, [*CATALOG_SHAPED, "imported_at:ts", "has_pan:bool"]),
    "catalog_signals": (
        ["row_key", "kind", "ord"],
        5000,
        ["row_key:text", "kind:text", "ord:int", "evidence_on:day?", "rank:int?", "grade:text", "note:text", "payload:json"],
    ),
    "catalog_posted": (["sd"], 1000, [
        "sd:text", "feishu_record:text", "title:text", "title_key:text", "lang:text", "platform:text", "life:text",
        "scheduled:bool", "online_on:day?", "why:text", "note:text", "archived:bool", "post_count:int", "last_post_on:day?",
        "views_total:int", "sources:text[]", "cats:text[]", "who:text[]", "accounts:text[]", "created_on:day?",
        "updated_on:day?", "first_post_on:day?", "metric_at:day?", "sched_count:int", "views_count:int", "posts:json",
        "row_keys:text[]", "drama_ids:text[]", "imported_at:ts",
    ]),
    "catalog_accounts": (["id"], 1000, [
        "id:text", "name:text", "url:text", "grp:text", "form:text", "niche:text", "status:text", "fans:int?", "as_of:day?", "imported_at:ts",
    ]),
    "rs_rows": (["drama_id"], 2000, [
        *CATALOG_SHAPED,
        "has_pan:bool", "rs_clk:bool", "rs_bill:bool", "rs_gsc:bool", "rs_clk_on:day?", "rs_bill_on:day?", "rs_gsc_on:day?",
        "drama_id:text",
        "locale:text", "slug:text", "publish_at:ts?", "chapter_count:int", "pay_start_raw:int", "rr:float", "promoters_cnt:int",
        "metrics_valid:bool?", "synced_at:ts?", "search_impressions:int", "search_data_at:ts?", "detail_synced_at:ts?",
        "tag_list:text[]", "description:text",
        "baseline1_at:ts?", "baseline7_at:ts?", "baseline15_at:ts?", "rr1:float?", "p1:int?", "rr7:float?", "p7:int?",
        "rr15:float?", "p15:int?", "s1_rr:float?", "s1_p:int?", "s7_rr:float?", "s7_p:int?", "clicks7:int",
        "last_click_on:day?", "bill_orders:int", "last_bill_on:day?", "bill_rank:int?",
    ]),
    "rs_ids": (["id"], 10000, [
        "id:text", "canonical_id:text?", "locale:text", "slug:text", "title:text", "chapter_count:int", "pay_start:int", "is_public_canonical:bool",
    ]),
    "rs_clicks14": (["drama_id", "day"], 20000, ["drama_id:text", "day:day", "human:int", "bot:int"]),
    "rs_bill_orders": (["bill_date", "book_id", "promotion_type"], 5000, [
        "bill_date:day", "book_id:text", "promotion_type:text", "canonical_id:text?", "book_title:text", "order_cnt:int", "source_rows:int",
        "same_day_clicks:int",
    ]),
    "rs_series_day": (["drama_id"], 40000, ["drama_id:text", "revenue_cents:float", "promoters_cnt:int"]),
}  # fmt: skip
COUNTED = [name for name in SPECS if name != "rs_series_day"]  # export-v2.ts:420
# rs:src/lib/pick/export-v2-page.ts:75-78
ALLOWED = {"manifest": {"as_of"}, "rs_series_day": {"as_of", "fp", "cursor", "limit", "day"}}
ROW_PARAMS = {"as_of", "fp", "cursor", "limit"}
DEFAULT_SIZES = {
    "catalog_rows": 3,
    "catalog_signals": 2,
    "catalog_posted": 1,
    "catalog_accounts": 0,
    "rs_rows": 2,
    "rs_ids": 2,
    "rs_clicks14": 1,
    "rs_bill_orders": 1,
}


class Clock:
    """Wall clock, monotonic timer and sleep in one: sleeping moves both, so as_of re-selection sees the wait."""

    def __init__(self, start: datetime = START):
        self.now = start
        self.sleeps: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    def timer(self) -> float:
        return (self.now - START).total_seconds()

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.advance(seconds)


@dataclass(frozen=True)
class Call:
    resource: str  # "manifest", a row resource, or "v1"
    n: int  # how many times this resource has been asked for, this call included
    params: dict[str, str]
    headers: dict[str, str]


Reply = tuple[int, dict[str, str], bytes]


def _json(status: int, body, headers: dict[str, str] | None = None) -> Reply:
    # Response.json in rs:src/lib/pick/feed-http.ts:38-40: compact JSON, cache-control no-store.
    data = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    return status, {"content-type": "application/json", "cache-control": "no-store", **(headers or {})}, data


def v2_error(status: int, error: str, headers: dict[str, str] | None = None, **extra) -> Reply:
    return _json(status, {"ok": False, "version": EXPORT_VERSION, "error": error, **extra}, headers)


def v1_error(status: int, error: str, headers: dict[str, str] | None = None) -> Reply:
    return _json(status, {"ok": False, "error": error}, headers)


def busy(v2: bool = True) -> Reply:
    # rs:src/lib/pick/export-v2.ts:72 and feed-http.ts:43-45: Retry-After is always 60.
    return (v2_error if v2 else v1_error)(503, "source_busy", {"retry-after": "60"})


def _cell(name: str, kind: str, i: int):
    base = kind.rstrip("?")
    values = {
        "text": f"{ROW_SENTINEL}-{name}-{i}",
        "day": "2026-09-20",
        "int": i,
        "float": i + 0.5,
        "bool": i % 2 == 0,
        "ts": "2026-09-20T01:02:03.000Z",
        "text[]": [f"{ROW_SENTINEL}-{name}-{i}"],
        "json": {"d": f"{ROW_SENTINEL}-{i}"} if name == "payload" else [{"note": f"{ROW_SENTINEL}-{i}"}],
    }
    return values[base]


def make_rows(resource: str, n: int) -> list[dict]:
    keys, _, columns = SPECS[resource]
    rows = []
    for i in range(n):
        row = {column.split(":")[0]: _cell(*column.split(":"), i) for column in columns}
        for key in keys:
            kind = next(c.split(":")[1] for c in columns if c.split(":")[0] == key)
            row[key] = i if kind == "int" else "2026-09-20" if kind == "day" else f"{resource}-key-{i:05d}"
        rows.append(row)
    return sorted(rows, key=lambda row: tuple(row[k] for k in keys))


def feed_row(i: int) -> dict:
    # rs:src/lib/pick/feed-map.ts:165-178 (FeedRow), values as tests/test_realshort_sync.py:12-28 builds them.
    return {
        "source": "realshort-pick",
        "source_id": f"{ROW_SENTINEL}-id-{i}",
        "language": "en",
        "title": f"{ROW_SENTINEL}-title-{i}",
        "theater": "KalosTV",
        "tags": [],
        "listed_at": "2026-09-01",
        "availability": "unknown",
        "signals": [],
        "channel_rules": {"youtube": "unknown"},
        "detail_url": f"{ROW_SENTINEL}-ref-{i}",
        "posted": {"matched": False, "records": [], "post_count": 0, "sched_count": 0, "last_post_on": None, "accounts": []},
    }


def encode_cursor(resource: str, key: list) -> str:
    # rs:src/lib/pick/export-v2-page.ts:158-163: base64url of JSON.stringify({r, k}), no padding.
    raw = json.dumps({"r": resource, "k": key}, ensure_ascii=False, separators=(",", ":")).encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def decode_cursor(resource: str, raw: str) -> list | None:
    try:
        parsed = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except ValueError:
        return None
    ok = isinstance(parsed, dict) and parsed.get("r") == resource and isinstance(parsed.get("k"), list)
    return parsed["k"] if ok and encode_cursor(resource, parsed["k"]) == raw else None


def parse_as_of(raw: str | None, now: datetime) -> str | None:
    """rs:src/lib/pick/export-v2-page.ts:27-35: whole minutes, Z, not after now, not older than 30 minutes."""
    match = AS_OF_RE.match(raw or "")
    if not match:
        return None
    try:
        moment = datetime(*(int(part) for part in match.groups()), tzinfo=UTC)
    except ValueError:
        return None
    if not now - AS_OF_MAX_AGE <= moment <= now:
        return None
    return moment.strftime("%Y-%m-%dT%H:%M:00.000Z")


def meta(scrub: dict, warnings: list) -> dict:
    """META_SHAPE, rs:src/lib/pick/export-v2-map.ts:878-897; values are minimal but every key is there."""
    facets = {"platforms": {}, "langs": [], "bases": {}, "posted": {"pool": 0, "yes": 0, "no": 0}}
    rules_keys = ["platformRules", "inUse", "basisLabels", "basisDateLabels", "rsRankLabels", "youtubeLabels", "glossary", "ruleHints", "langLoc"]
    return {
        "freshness": dict.fromkeys(["importedAt", "rows", "withSignal", "signals", "posted", "rsCanonical", "rsCandidates", "rsSyncedAt"], 0),
        "rsCounts": dict.fromkeys(["all", "cand", "growthD1", "growthD7", "growthDp1", "growthDp7", "pc", "clk", "gsc", "bill", "ledger"], 0),
        "growthBaseline": {k: {"baselineDay": None, "baselineSnapshot": None, "earliestVerifiedOn": None} for k in ("1", "7")},
        "sources": {},
        "rules": {**{k: {} for k in rules_keys}, "inUse": [], "glossary": [], "postedPoolUrl": "", "sortLabels": {}},
        "control": {
            "facetsPick": facets,
            "facetsAll": facets,
            "rankCounts": {},
            "postedStats": dict.fromkeys(["total", "pubCount", "postsSum", "viewsSum", "metricAt", "importedAt", "accountCount"], 0),
            "postedStates": {"pub": 0, "sched": 0, "none": 0, "nomatch": 0},
            "ledger": {"rows": 0, "orders": 0},
        },
        "scrub": scrub,
        "warnings": warnings,
    }


class FakeRealShort:
    """One RealShort deployment: tokens, optional deployment protection, a data generation behind the fingerprint."""

    def __init__(
        self,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
        export_token: str | None = EXPORT_TOKEN,
        feed_token: str | None = FEED_TOKEN,
        bypass: str | None = None,
        sizes: dict[str, int] | None = None,
        series: dict[str, int] | None = None,
        v1_rows: int = 3,
        scrub: dict | None = None,
        warnings: list | None = None,
        sha: str | None = "sha-test",
        page_rows: dict[str, int] | None = None,
        counts: dict[str, int] | None = None,
        intercept: Callable[[Call], Reply | None] | None = None,
    ):
        self.now = now
        self.export_token, self.feed_token, self.bypass, self.sha = export_token, feed_token, bypass, sha
        self.data = {name: make_rows(name, n) for name, n in {**DEFAULT_SIZES, **(sizes or {})}.items()}
        days = {"2026-09-22": 2, "2026-09-23": 3} if series is None else series
        self.series = {day: make_rows("rs_series_day", n) for day, n in sorted(days.items())}
        self.v1 = [(f"k{i:04d}", feed_row(i)) for i in range(v1_rows)]
        self.scrub, self.warnings = scrub or {}, warnings or []
        self.page_rows, self.counts, self.intercept = page_rows or {}, counts, intercept
        self.generation = 0
        self.calls: list[Call] = []
        self.raw_paths: list[str] = []  # every request that reached the deployment, deployment protection included
        self._lock = threading.Lock()

    def fingerprint(self) -> str:
        return hashlib.sha256(f"{self.generation}:{self.sha}".encode()).hexdigest()

    def transport(self) -> httpx.MockTransport:
        def handler(request: httpx.Request) -> httpx.Response:
            headers = {k.lower(): v for k, v in request.headers.items()}
            status, out, body = self.respond(request.url.path, list(request.url.params.multi_items()), headers)
            # Raw header bytes, as a socket would deliver them; httpx refuses non-ASCII str values.
            return httpx.Response(status, headers={name: value.encode() for name, value in out.items()}, content=body)

        return httpx.MockTransport(handler)

    def respond(self, path: str, query: list[tuple[str, str]], headers: dict[str, str]) -> Reply:
        with self._lock:
            return self._respond(path, query, headers)

    def _respond(self, path: str, query: list[tuple[str, str]], headers: dict[str, str]) -> Reply:
        self.raw_paths.append(path)
        if self.bypass is not None and headers.get("x-vercel-protection-bypass") != self.bypass:
            # Vercel deployment protection answers before the route: a redirect to SSO (plan P1-4).
            return 307, {"location": "https://vercel.com/sso-api?url=x"}, b""
        v2 = path.startswith("/api/pick-feed/v2/")
        resource = path.removeprefix("/api/pick-feed/v2/") if v2 else "v1"
        if not v2 and path != "/api/pick-feed":
            return 404, {}, b"not found"
        call = Call(resource, 1 + sum(c.resource == resource for c in self.calls), dict(query), headers)
        self.calls.append(call)
        token = self.export_token if v2 else self.feed_token
        error = v2_error if v2 else v1_error
        if not (token or "").strip():
            return error(404, "not_found")
        if headers.get("authorization") != f"Bearer {token.strip()}":
            return error(401, "unauthorized")
        return self._v2(call, query) if v2 else self._v1(call, query)

    def _v2(self, call: Call, query: list[tuple[str, str]]) -> Reply:
        """rs:src/lib/pick/export-v2-page.ts:94-112 then export-v2.ts:526-538."""
        resource, params = call.resource, call.params
        if resource != "manifest" and resource not in SPECS:
            return v2_error(400, "bad_request", reason="resource")
        allowed = ALLOWED.get(resource, ROW_PARAMS)
        if any(k not in allowed for k, _ in query):
            return v2_error(400, "bad_request", reason="unknown_param")
        if len(query) != len(params):
            return v2_error(400, "bad_request", reason="duplicate_param")
        as_of = parse_as_of(params.get("as_of"), self.now())
        if as_of is None:
            return v2_error(400, "bad_request", reason="as_of")
        if resource == "manifest":
            return self.intercept_or(call) or self._manifest(as_of)
        if not FP_RE.match(params.get("fp", "")):
            return v2_error(400, "bad_request", reason="fp")
        problem = self._row_query_problem(resource, params, as_of)
        if problem:
            return v2_error(400, "bad_request", reason=problem)
        return self.intercept_or(call) or self._rows(resource, params, as_of)

    def _row_query_problem(self, resource: str, params: dict[str, str], as_of: str) -> str | None:
        if "cursor" in params and decode_cursor(resource, params["cursor"]) is None:
            return "cursor"
        limit = params.get("limit", str(SPECS[resource][1]))
        if not re.fullmatch(r"[0-9]{1,6}", limit) or not 1 <= int(limit) <= SPECS[resource][1]:
            return "limit"
        if resource == "rs_series_day":
            last = date.fromisoformat(as_of[:10])
            first = last - timedelta(days=SERIES_DAY_SPAN - 1)
            if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", params.get("day", "")) or not first.isoformat() <= params["day"] <= last.isoformat():
                return "day"
        return None

    def intercept_or(self, call: Call) -> Reply | None:
        return self.intercept(call) if self.intercept else None

    def _manifest(self, as_of: str) -> Reply:
        fp = self.fingerprint()
        days = [{"day": day, "rows": len(rows)} for day, rows in self.series.items()]
        row = {
            "version": EXPORT_VERSION,
            "asOf": as_of,
            "fingerprint": fp,
            "sourceRevision": self.sha,
            "counts": self.counts or {name: len(self.data[name]) for name in COUNTED},
            "latestSnapshot": days[-1]["day"] if days else None,
            "snapshotDays": days,
            "meta": meta(self.scrub, self.warnings),
        }
        return _json(200, {"ok": True, "version": EXPORT_VERSION, "resource": "manifest", "asOf": as_of, "fingerprint": fp, "rows": [row], "nextCursor": None})

    def _rows(self, resource: str, params: dict[str, str], as_of: str) -> Reply:
        if params["fp"] != self.fingerprint():
            return v2_error(409, "source_changed")
        keys, max_limit, _ = SPECS[resource]
        rows = self.series.get(params.get("day", ""), []) if resource == "rs_series_day" else self.data[resource]
        after = decode_cursor(resource, params["cursor"]) if "cursor" in params else None
        pending = [row for row in rows if after is None or [row[k] for k in keys] > after]
        # The byte cut (export-v2-page.ts:207-229) can end a page early; page_rows stands in for it.
        take = min(int(params.get("limit", max_limit)), self.page_rows.get(resource, max_limit))
        chunk = pending[:take]
        more = len(pending) > len(chunk) and len(chunk) > 0
        body = {
            "ok": True,
            "version": EXPORT_VERSION,
            "resource": resource,
            "asOf": as_of,
            "fingerprint": params["fp"],
            "rows": chunk,
            "nextCursor": encode_cursor(resource, [chunk[-1][k] for k in keys]) if more else None,
        }
        return _json(200, body)

    def _v1(self, call: Call, query: list[tuple[str, str]]) -> Reply:
        """rs:src/lib/pick/feed-map.ts:46-63 (parseFeedQuery), feed.ts:73-99 (loadFeedPage)."""
        params = call.params
        if len(query) != len(params) or len(params.get("cursor", "")) > 512:
            return v1_error(400, "bad_request")
        as_of = parse_as_of(params["as_of"], self.now()) if "as_of" in params else None
        fp = params.get("fp")
        if ("as_of" in params and as_of is None) or (fp is not None and (as_of is None or not FP_RE.match(fp))):
            return v1_error(400, "bad_request")
        limit = params.get("limit", "500")
        if not re.fullmatch(r"\d{1,4}", limit) or not 1 <= int(limit) <= 1000:
            return v1_error(400, "bad_request")
        intercepted = self.intercept_or(call)
        if intercepted:
            return intercepted
        if fp is not None and fp != self.fingerprint():
            return v1_error(409, "source_changed")
        return self._v1_page(params.get("cursor", ""), int(limit), as_of)

    def _v1_page(self, cursor: str, limit: int, as_of: str | None) -> Reply:
        pending = [(key, row) for key, row in self.v1 if key > cursor]
        chunk = pending[:limit]
        first = not cursor
        body = {
            "ok": True,
            "version": FEED_VERSION,
            "capturedAt": as_of or self.now().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "scope": "RealShort选剧候选池：有来源信号且未标注下架；不是全部剧库",
            "total": len(self.v1) if first else None,
            "sourceRevision": self.sha,
            "freshness": {"catalogImportedAt": None, "reelshortSyncedAt": None, "catalogRows": 0, "withSignal": 0, "postedRecords": 0} if first else None,
            "rules": "# RealShort 选剧规则与口径" if first else None,
            "rows": [row for _, row in chunk],
            "nextCursor": chunk[-1][0] if len(pending) > limit else None,
            "fingerprint": self.fingerprint(),
        }
        return _json(200, body)


class _Handler(BaseHTTPRequestHandler):
    fake: FakeRealShort

    def do_GET(self):
        parts = urlsplit(self.path)
        headers = {k.lower(): v for k, v in self.headers.items()}
        status, out, body = self.fake.respond(parts.path, parse_qsl(parts.query, keep_blank_values=True), headers)
        self.send_response(status)
        for name, value in {**out, "content-length": str(len(body))}.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def serve(fake: FakeRealShort) -> tuple[ThreadingHTTPServer, str]:
    """The double on a loopback port, for a client in another process; stop with server.shutdown()."""
    handler = type("Handler", (_Handler,), {"fake": fake})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"
