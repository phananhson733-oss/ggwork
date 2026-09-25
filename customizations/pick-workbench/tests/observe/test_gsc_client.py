"""TR-06: the GSC client, service-account JWT, searchAnalytics.query and its metadata (design 5.1; plan TR-06).

All of it runs against tests/observe/gsc_fake.py over httpx.MockTransport; no request reaches Google (TR-07's P1 makes
the first real call). Keys are generated in the test, never read from a file (plan 5: fixtures ship no key). What is
copied from RealShort (rs = realshort-pick-export-v2 816ca2e) names its line; the quota error bodies are built from
Google's documented envelope and are replaced by TR-07's recorded ones.
"""

import json
import logging
import subprocess
import sys
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import httpx
import jwt
import pytest
from gsc_fake import DAY, EMAIL, NOW, SITE, FakeGoogle, as_env, env, google_error, key_lines, new_key, oauth_error, ok, pkcs8, row, sent_body

from ggwork_pick.observe.clock import ManualClock
from ggwork_pick.observe.errors import ExitCode, describe_error
from ggwork_pick.observe.gsc import auth
from ggwork_pick.observe.gsc.auth import GscConfigError
from ggwork_pick.observe.gsc.client import GscClient, GscRequestError, load_config
from ggwork_pick.observe.gsc.query import ROW_LIMIT, GscQuery, ResponseShapeError, parse_response, query_body

SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
SOURCE = Path(__file__).resolve().parents[2]  # customizations/pick-workbench


@pytest.fixture(scope="module")
def key():
    return new_key()


def a_query(dimensions=("hour",), data_state="hourly_all", **extra) -> GscQuery:
    return GscQuery(start_date=DAY, end_date=DAY, dimensions=dimensions, data_state=data_state, **extra)


def a_client(fake: FakeGoogle, key, clock: ManualClock | None = None, **extra) -> GscClient:
    return GscClient(load_config(env(key)), transport=fake.transport(), clock=clock or ManualClock(NOW), **extra)


async def failure(fake: FakeGoogle, key, query: GscQuery | None = None) -> GscRequestError:
    async with a_client(fake, key) as gsc:
        with pytest.raises(GscRequestError) as raised:
            await gsc.query(query or a_query())
    return raised.value


# ---- the private key (rs:src/lib/gsc-encoding.ts:11-45) -------------------------------------------------------------


def test_private_key_normalize(key):
    pem = pkcs8(key)
    # Quotes pasted along from a .env, a literal backslash-n from a one-line variable, whitespace: all undone.
    for raw in (as_env(pem), as_env(pem, "'"), f"  {pem}\n", pem.strip().replace("\n", "\\n")):
        assert auth.normalize_private_key(raw).strip() == pem.strip()
        loaded = auth.load_private_key(raw, source=auth.KEY_ENV)
        assert loaded.public_key().public_numbers() == key.public_key().public_numbers()
    # Exactly RealShort's rule: one quote off each end, every literal \n turned into a newline, nothing else touched.
    assert auth.normalize_private_key('  "a\\nb"  ') == "a\nb"
    assert auth.normalize_private_key("'x'") == "x"
    assert auth.normalize_private_key('""x""') == '"x"'
    assert auth.normalize_private_key("a\nb") == "a\nb"


def test_private_key_only_pkcs8(key):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from gsc_fake import pkcs1

    with pytest.raises(GscConfigError, match="PKCS#8") as refused:
        auth.load_private_key(as_env(pkcs1(key)), source=auth.KEY_ENV)
    assert "BEGIN RSA PRIVATE KEY" in str(refused.value) and auth.KEY_ENV in str(refused.value)
    assert refused.value.exit_code == ExitCode.REFUSED
    encrypted = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.BestAvailableEncryption(b"pw"))
    with pytest.raises(GscConfigError, match="PKCS#8"):
        auth.load_private_key(encrypted.decode(), source=auth.KEY_ENV)
    with pytest.raises(GscConfigError, match="RSA"):
        auth.load_private_key(pkcs8(ec.generate_private_key(ec.SECP256R1())), source=auth.KEY_ENV)
    damaged = pkcs8(key).replace(key_lines(pkcs8(key))[3], "!" * 64)
    with pytest.raises(GscConfigError, match="解不开"):
        auth.load_private_key(damaged, source=auth.KEY_ENV)
    with pytest.raises(GscConfigError, match="解不开"):
        auth.load_private_key("-----BEGIN PRIVATE KEY-----\nnot-base64\n-----END PRIVATE KEY-----", source=auth.KEY_ENV)


# ---- the assertion (rs:src/lib/gsc-api.ts:45-104) -------------------------------------------------------------------


def test_jwt_claims(key):
    account = auth.ServiceAccount(email=EMAIL, key=key)
    assertion = account.sign_assertion(NOW)
    assert jwt.get_unverified_header(assertion) == {"alg": "RS256", "typ": "JWT"}
    options = {"verify_exp": False, "verify_iat": False, "verify_nbf": False}
    claims = jwt.decode(assertion, key.public_key(), algorithms=["RS256"], audience=auth.TOKEN_ENDPOINT, options=options)
    iat = int(NOW.timestamp())
    assert claims == {"iss": EMAIL, "scope": SCOPE, "aud": "https://oauth2.googleapis.com/token", "iat": iat, "exp": iat + 3600}
    assert auth.SCOPE == SCOPE  # read-only: never the read-write webmasters scope
    with pytest.raises(jwt.InvalidSignatureError):
        jwt.decode(assertion, new_key().public_key(), algorithms=["RS256"], audience=auth.TOKEN_ENDPOINT, options=options)
    with pytest.raises(ValueError):
        account.sign_assertion(datetime(2026, 9, 25, 3, 25))  # naive: no telling which instant it is


@pytest.mark.asyncio
async def test_token_exchange_on_the_wire(key):
    fake = FakeGoogle(key.public_key())
    clock = ManualClock(NOW)
    async with a_client(fake, key, clock) as gsc:
        await gsc.query(a_query())
        await gsc.query(a_query())
        assert len(fake.token_calls) == 1  # one token for both queries
        clock.advance(3599 - 300)  # within five minutes of its expiry: a new one
        await gsc.query(a_query())
    assert len(fake.token_calls) == 2 and len(fake.query_calls) == 3
    assert [claims["iat"] for claims in fake.claims] == [int(NOW.timestamp()), int(NOW.timestamp()) + 3299]
    assert {claims["scope"] for claims in fake.claims} == {SCOPE} and {claims["iss"] for claims in fake.claims} == {EMAIL}
    assert [call.headers["authorization"] for call in fake.query_calls] == ["Bearer ya29.test-token-1"] * 2 + ["Bearer ya29.test-token-2"]


@pytest.mark.asyncio
async def test_401_refreshes_the_token_once(key):
    fake = FakeGoogle(key.public_key(), query_replies=[httpx.Response(401), ok([row("2026-09-24T01:00:00-07:00")])])
    async with a_client(fake, key) as gsc:
        response = await gsc.query(a_query())
    assert len(response.rows) == 1 and len(fake.token_calls) == 2
    assert [call.headers["authorization"] for call in fake.query_calls] == ["Bearer ya29.test-token-1", "Bearer ya29.test-token-2"]

    twice = FakeGoogle(key.public_key(), query_replies=[httpx.Response(401), httpx.Response(401)])
    error = await failure(twice, key)
    assert (error.kind, error.status, error.endpoint) == ("unauthorized", 401, "query")
    assert len(twice.token_calls) == 2 and len(twice.query_calls) == 2


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply, hint",
    [
        (oauth_error(400, "invalid_grant", "Invalid JWT Signature."), "时钟"),
        (oauth_error(401, "unauthorized_client", "Client is unauthorized to retrieve access tokens."), "Search Console API"),
        (oauth_error(400, "invalid_client", "The OAuth client was not found."), "服务账号"),
        (httpx.Response(200, json={"token_type": "Bearer"}), "access_token"),
    ],
)
async def test_token_rejected(key, reply, hint):
    fake = FakeGoogle(key.public_key(), token_replies=[reply])
    error = await failure(fake, key)
    assert (error.endpoint, error.kind) == ("token", "token_rejected" if reply.status_code != 200 else "malformed")
    assert hint in str(error) and len(fake.query_calls) == 0
    # Google's description is kept for a probe to print, never in the message a log line shows.
    assert "Invalid JWT" not in str(error) and "Client is unauthorized" not in str(error)


# ---- the request body (rs:src/lib/gsc-encoding.ts:47-76, gsc-api.ts:118-143) ------------------------------------------


def test_query_body():
    groups = [
        {
            "groupType": "and",
            "filters": [{"dimension": "page", "operator": "includingRegex", "expression": r"^https://dramashortstv\.com/en/drama/[a-z0-9-]+$"}],
        }
    ]
    query = a_query(("hour", "country"), aggregation_type="byPage", dimension_filter_groups=groups)
    body = query_body(query)
    assert body == {
        "startDate": "2026-09-24",
        "endDate": "2026-09-24",
        "dimensions": ["hour", "country"],
        "type": "web",
        "dataState": "hourly_all",
        "aggregationType": "byPage",
        "dimensionFilterGroups": groups,
        "rowLimit": 25000,
    }
    assert ROW_LIMIT == 25000 and "startRow" not in body  # fresh data is never paged (design 5.2)
    # Verbatim, and a copy: the caller changing its list afterwards changes nothing that is sent.
    groups[0]["filters"][0]["expression"] = "changed"
    assert query_body(query)["dimensionFilterGroups"][0]["filters"][0]["expression"] != "changed"
    plain = query_body(a_query(("date", "page", "country"), "final"))
    assert plain["dataState"] == "final" and plain["rowLimit"] == 25000
    assert "aggregationType" not in plain and "dimensionFilterGroups" not in plain


@pytest.mark.parametrize(
    "change",
    [
        {"dimensions": ("hour", "hour")},
        {"dimensions": ("hour", "pages")},
        {"data_state": "fresh"},
        {"data_state": None},
        {"dimensions": ("date",), "data_state": "hourly_all"},  # the hour dimension and hourly_all come as a pair (design 5.2)
        {"dimensions": ("hour",), "data_state": "all"},
        {"start_date": date(2026, 9, 25)},
        {"start_date": "2026-09-24"},
        {"aggregation_type": "bypage"},
        {"dimension_filter_groups": [{"groupType": "or", "filters": [{"dimension": "page", "expression": "x"}]}]},
        {"dimension_filter_groups": [{"filters": []}]},
        {"dimension_filter_groups": [{"filters": [{"dimension": "date", "expression": "2026-09-24"}]}]},
        {"dimension_filter_groups": [{"filters": [{"dimension": "page", "operator": "regex", "expression": "x"}]}]},
        {"dimension_filter_groups": [{"filters": [{"dimension": "page", "expression": ""}]}]},
        {"dimension_filter_groups": [{"filters": [{"dimension": "page", "expression": "x", "extra": 1}]}]},
        {"dimension_filter_groups": [{"filters": [{"dimension": "page", "expression": "x"}], "extra": 1}]},
        {"dimension_filter_groups": "page"},
    ],
)
def test_query_refuses_bad_shapes(change):
    fields = {"start_date": DAY, "end_date": DAY, "dimensions": ("hour",), "data_state": "hourly_all", **change}
    with pytest.raises(ValueError):
        GscQuery(**fields)


@pytest.mark.asyncio
async def test_query_on_the_wire(key):
    fake = FakeGoogle(key.public_key(), query_replies=[ok()])
    query = a_query(("date", "page", "country"), "all")
    async with a_client(fake, key) as gsc:
        await gsc.query(query)
    (call,) = fake.query_calls
    assert call.method == "POST" and call.headers["content-type"] == "application/json"
    assert call.url.raw_path == b"/webmasters/v3/sites/sc-domain%3Adramashortstv.com/searchAnalytics/query"
    assert sent_body(call) == query_body(query)
    prefix = FakeGoogle(key.public_key(), query_replies=[ok()])
    config = load_config(env(key, PICK_GSC_SITE_URL="https://dramashortstv.com/"))
    async with GscClient(config, transport=prefix.transport(), clock=ManualClock(NOW)) as gsc:
        await gsc.query(query)
    assert prefix.query_calls[0].url.raw_path == b"/webmasters/v3/sites/https%3A%2F%2Fdramashortstv.com%2F/searchAnalytics/query"


# ---- the response -----------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [ROW_LIMIT - 1, ROW_LIMIT])
async def test_truncated_flag(key, count):
    rows = [row(f"https://dramashortstv.com/en/drama/{number}") for number in range(count)]
    fake = FakeGoogle(key.public_key(), query_replies=[ok(rows)])
    async with a_client(fake, key) as gsc:
        response = await gsc.query(a_query(("page",), "all"))
    assert len(response.rows) == count
    assert response.truncated is (count == ROW_LIMIT)
    assert response.request_status == ("truncated" if count == ROW_LIMIT else "fetched")


def test_truncated_flag_bounds():
    query = a_query(("page",), "all")
    empty = parse_response(query, {"responseAggregationType": "byPage"}, fetched_at=NOW)
    # No rows field: nothing was observed in what GSC returned, which is not a zero (design 5.3).
    assert empty.rows == () and not empty.truncated and empty.request_status == "fetched"
    over = {"rows": [row(f"/p/{number}") for number in range(ROW_LIMIT + 1)]}
    with pytest.raises(ResponseShapeError, match="rowLimit"):
        parse_response(query, over, fetched_at=NOW)


def test_rows_parsed_strictly():
    query = a_query(("hour", "page", "country"))
    good = parse_response(query, {"rows": [row("2026-09-24T01:00:00-07:00", "https://x/", "usa", clicks=2.0, impressions=30)]}, fetched_at=NOW)
    (parsed,) = good.rows
    assert parsed.keys == ("2026-09-24T01:00:00-07:00", "https://x/", "usa")
    assert (parsed.clicks, parsed.impressions, parsed.ctr, parsed.position) == (2, 30, 0.1, 4.5)
    assert isinstance(parsed.clicks, int) and good.fetched_at == NOW and good.query is query
    for bad in (
        {"rows": [row("2026-09-24T01:00:00-07:00", "https://x/")]},  # fewer keys than dimensions
        {"rows": [row("a", "b", "c", clicks=1.5)]},
        {"rows": [row("a", "b", "c", impressions=-1)]},
        {"rows": [row("a", "b", "c", impressions=True)]},
        {"rows": [row("a", "b", "c", position=float("inf"))]},
        {"rows": [{"keys": ["a", "b", "c"], "clicks": 1, "impressions": 2, "ctr": 0.5}]},
        {"rows": [{"keys": ["a", "b", 3], "clicks": 1, "impressions": 2, "ctr": 0.5, "position": 1}]},
        {"rows": "none"},
        {"rows": [], "responseAggregationType": 3},
        [],
    ):
        with pytest.raises(ResponseShapeError):
            parse_response(query, bad, fetched_at=NOW)


def test_metadata_parsed():
    query = a_query(("hour",))
    metadata = {"first_incomplete_hour": "2026-09-24T17:00:00-07:00", "first_incomplete_date": "2026-09-24"}
    response = parse_response(
        query, {"rows": [row("2026-09-24T16:00:00-07:00")], "metadata": metadata, "responseAggregationType": "byProperty"}, fetched_at=NOW
    )
    assert response.metadata.first_incomplete_hour == datetime(2026, 9, 24, 17, tzinfo=timezone(timedelta(hours=-7)))
    assert response.metadata.first_incomplete_hour == datetime(2026, 9, 25, 0, tzinfo=UTC)
    assert response.metadata.first_incomplete_date == date(2026, 9, 24)
    assert dict(response.metadata.raw) == metadata  # stored with the response (design 5.3)
    assert response.aggregation == "byProperty"  # TR-07 P2 reads whether byPage was honoured from this
    bare = parse_response(query, {}, fetched_at=NOW)
    assert (bare.metadata.first_incomplete_hour, bare.metadata.first_incomplete_date, dict(bare.metadata.raw)) == (None, None, {})
    assert bare.aggregation is None
    # The REST reference spells the fields in snake_case; the camelCase JSON name is taken too until TR-07 sees a real one.
    camel = parse_response(query, {"metadata": {"firstIncompleteHour": "2026-09-24T17:00:00-07:00"}}, fetched_at=NOW)
    assert camel.metadata.first_incomplete_hour == datetime(2026, 9, 25, 0, tzinfo=UTC)
    for bad in (
        {"first_incomplete_hour": "2026-09-24T17:00:00"},  # no offset: which hour is it?
        {"first_incomplete_hour": "yesterday"},
        {"first_incomplete_date": "2026-9-24"},
        {"first_incomplete_date": 20260924},
        {"first_incomplete_hour": "2026-09-24T17:00:00-07:00", "firstIncompleteHour": "2026-09-24T18:00:00-07:00"},
    ):
        with pytest.raises(ResponseShapeError, match="metadata"):
            parse_response(query, {"metadata": bad}, fetched_at=NOW)


@pytest.mark.asyncio
async def test_malformed_response_is_a_failure(key):
    for reply in (httpx.Response(200, content=b"<html>"), ok([row("a", "b")]), httpx.Response(200, json={"metadata": {"first_incomplete_date": "x"}})):
        error = await failure(FakeGoogle(key.public_key(), query_replies=[reply]), key)
        assert (error.kind, error.status) == ("malformed", 200)


# ---- errors -----------------------------------------------------------------------------------------------------------

SHORT = "Search Analytics load quota exceeded (short-term). Learn more at https://developers.google.com/webmaster-tools/limits"
LONG = "Search Analytics load quota exceeded (long-term). Learn more at https://developers.google.com/webmaster-tools/limits"
QPM = "Quota exceeded for quota metric 'Queries' and limit 'Queries per minute' of service 'searchconsole.googleapis.com'"
QPD = "Quota exceeded for quota metric 'Queries' and limit 'Queries per day' of service 'searchconsole.googleapis.com'"
PER_DAY_INFO = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": "RATE_LIMIT_EXCEEDED", "metadata": {"quota_limit": "defaultPerDayPerProject"}}]
NO_PERMISSION = (
    "User does not have sufficient permission for site 'sc-domain:dramashortstv.com'. See also: https://support.google.com/webmasters/answer/2451999."
)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply, kind",
    [
        (google_error(429, "RESOURCE_EXHAUSTED", SHORT, reason="rateLimitExceeded"), "quota_short"),
        (google_error(429, "RESOURCE_EXHAUSTED", QPM, reason="rateLimitExceeded"), "quota_short"),
        (google_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded.", reason="quotaExceeded"), "quota_short"),
        (httpx.Response(429, content=b"Too Many Requests"), "quota_short"),
        (google_error(429, "RESOURCE_EXHAUSTED", LONG, reason="quotaExceeded"), "quota_long"),
        (google_error(429, "RESOURCE_EXHAUSTED", QPD, reason="rateLimitExceeded"), "quota_long"),
        (google_error(429, "RESOURCE_EXHAUSTED", "Quota exceeded.", details=PER_DAY_INFO), "quota_long"),
        (google_error(403, "PERMISSION_DENIED", "Daily Limit Exceeded", reason="dailyLimitExceeded"), "quota_long"),
        (google_error(403, "PERMISSION_DENIED", "Rate Limit Exceeded", reason="userRateLimitExceeded"), "quota_short"),
        (google_error(403, "PERMISSION_DENIED", NO_PERMISSION, reason="forbidden"), "forbidden"),
        (httpx.Response(403, content=b"<html>forbidden</html>"), "forbidden"),
    ],
)
async def test_quota_error_classified(key, reply, kind):
    fake = FakeGoogle(key.public_key(), query_replies=[reply])
    error = await failure(fake, key)
    assert (error.kind, error.status, error.endpoint) == (kind, reply.status_code, "query")
    assert error.is_quota is kind.startswith("quota_")
    assert error.exit_code == ExitCode.FAILED
    assert len(fake.query_calls) == 1  # never retried here: the quota is shared with RealShort (design 5.1)
    if kind == "forbidden":
        # RealShort's 403 lesson (rs:src/lib/gsc-api.ts:112-117): the permission text also means a wrong property name.
        assert "PICK_GSC_SITE_URL" in str(error) and "sc-domain:dramashortstv.com" in str(error) and "Owner" in str(error)
    if kind == "quota_long":
        assert "今天" in str(error)


@pytest.mark.asyncio
async def test_quota_error_keeps_retry_after_and_detail(key):
    reply = google_error(429, "RESOURCE_EXHAUSTED", SHORT, reason="rateLimitExceeded", headers={"retry-after": "120"})
    error = await failure(FakeGoogle(key.public_key(), query_replies=[reply]), key)
    assert error.retry_after == 120 and error.reasons == ("rateLimitExceeded",)
    assert error.detail == SHORT and SHORT not in str(error)  # for TR-07's probe to print, not for a log line


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply, kind",
    [
        (google_error(400, "INVALID_ARGUMENT", "Invalid regular expression", reason="invalidParameter"), "bad_request"),
        (google_error(404, "NOT_FOUND", "Requested entity was not found."), "not_found"),
        (httpx.Response(302, headers={"location": "https://accounts.google.com/"}), "redirect"),
        (google_error(500, "INTERNAL", "Internal error encountered."), "server_error"),
        (google_error(503, "UNAVAILABLE", "The service is currently unavailable."), "server_error"),
        (httpx.Response(418, content=b"teapot"), "http_error"),
        (httpx.ReadTimeout("slow"), "timeout"),
        (httpx.ConnectError("refused"), "connection"),
    ],
)
async def test_other_failures_classified(key, reply, kind):
    fake = FakeGoogle(key.public_key(), query_replies=[reply])
    error = await failure(fake, key)
    assert error.kind == kind and not error.is_quota and len(fake.query_calls) == 1
    assert error.status == (reply.status_code if isinstance(reply, httpx.Response) else None)


@pytest.mark.asyncio
async def test_body_over_the_cap_is_refused(key):
    fake = FakeGoogle(key.public_key(), query_replies=[ok([row(f"/p/{number}") for number in range(200)])])
    async with a_client(fake, key, max_body_bytes=1024) as gsc:
        with pytest.raises(GscRequestError) as raised:
            await gsc.query(a_query(("page",), "all"))
    assert raised.value.kind == "too_large"


@pytest.mark.asyncio
async def test_every_request_reported(key):
    seen = []
    replies = [ok([row("a")]), google_error(429, "RESOURCE_EXHAUSTED", SHORT, reason="rateLimitExceeded"), httpx.ConnectError("x")]
    fake = FakeGoogle(key.public_key(), query_replies=replies)
    async with a_client(fake, key, on_response=seen.append) as gsc:
        await gsc.query(a_query())
        for _ in range(2):
            with pytest.raises(GscRequestError):
                await gsc.query(a_query())
    assert [(item.endpoint, item.status, item.error) for item in seen] == [
        ("token", 200, None),
        ("query", 200, None),
        ("query", 429, "quota_short"),
        ("query", None, "connection"),
    ]
    assert seen[1].rows == 1 and seen[1].truncated is False and seen[2].rows is None


# ---- configuration (plan TR-06: PICK_GSC_SA_EMAIL, PICK_GSC_SA_PRIVATE_KEY, PICK_GSC_SA_FILE, PICK_GSC_SITE_URL) -------


def sa_file(tmp_path: Path, key, mode: int = 0o600, **change) -> Path:
    """The service-account JSON key GCP hands out (U1), with made-up ids."""
    document = {"type": "service_account", "project_id": "ggwork-test", "private_key_id": "0" * 40, "client_email": EMAIL, "private_key": pkcs8(key), **change}
    path = tmp_path / "sa-key.json"
    path.write_text(json.dumps(document))
    path.chmod(mode)
    return path


def test_credentials_from_env(key):
    config = load_config(env(key))
    assert config.site_url == SITE and config.account.email == EMAIL
    assert config.account.key.public_key().public_numbers() == key.public_key().public_numbers()
    for missing in ("PICK_GSC_SA_EMAIL", "PICK_GSC_SA_PRIVATE_KEY", "PICK_GSC_SITE_URL"):
        with pytest.raises(GscConfigError, match=missing) as refused:
            load_config({name: value for name, value in env(key).items() if name != missing})
        assert refused.value.exit_code == ExitCode.REFUSED
    with pytest.raises(GscConfigError, match="PICK_GSC_SA_EMAIL"):
        load_config(env(key, PICK_GSC_SA_EMAIL="not an email"))


def test_credentials_from_file(key, tmp_path):
    path = sa_file(tmp_path, key)
    config = load_config({"PICK_GSC_SA_FILE": str(path), "PICK_GSC_SITE_URL": SITE})
    assert config.account.email == EMAIL
    assert config.account.key.public_key().public_numbers() == key.public_key().public_numbers()
    with pytest.raises(GscConfigError, match="只设一种"):
        load_config({**env(key), "PICK_GSC_SA_FILE": str(path)})
    for mode in (0o640, 0o604, 0o644):
        path.chmod(mode)
        with pytest.raises(GscConfigError, match="600"):
            load_config({"PICK_GSC_SA_FILE": str(path), "PICK_GSC_SITE_URL": SITE})
    with pytest.raises(GscConfigError, match="不存在"):
        load_config({"PICK_GSC_SA_FILE": str(tmp_path / "missing.json"), "PICK_GSC_SITE_URL": SITE})
    for change in ({"type": "authorized_user"}, {"client_email": None}, {"private_key": 7}):
        with pytest.raises(GscConfigError, match="服务账号"):
            load_config({"PICK_GSC_SA_FILE": str(sa_file(tmp_path, key, **change)), "PICK_GSC_SITE_URL": SITE})
    not_json = tmp_path / "not-json"
    not_json.write_text("{")
    not_json.chmod(0o600)
    with pytest.raises(GscConfigError, match="服务账号"):
        load_config({"PICK_GSC_SA_FILE": str(not_json), "PICK_GSC_SITE_URL": SITE})


@pytest.mark.parametrize("site", ["sc-domain:dramashortstv.com", "https://dramashortstv.com/", "https://www.dramashortstv.com/en/"])
def test_site_url_accepted(key, site):
    assert load_config(env(key, PICK_GSC_SITE_URL=site)).site_url == site


@pytest.mark.parametrize("site", ["dramashortstv.com", "https://dramashortstv.com", "sc-domain:", "sc-domain:https://x.com/", "ftp://x.com/"])
def test_site_url_refused(key, site):
    # A wrong property name comes back as a 403 that reads like a missing grant (rs:src/lib/gsc-api.ts:112-117).
    with pytest.raises(GscConfigError, match="sc-domain:dramashortstv.com"):
        load_config(env(key, PICK_GSC_SITE_URL=site))


# ---- secrets ----------------------------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_key_in_logs(key, caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    pem = pkcs8(key)
    shown: list[str] = []
    config = load_config(env(key))
    file_config = load_config({"PICK_GSC_SA_FILE": str(sa_file(tmp_path, key)), "PICK_GSC_SITE_URL": SITE})
    shown += [repr(config), str(config), repr(config.account), repr(file_config)]
    # ok; 401 twice (unauthorized); 403 (forbidden); 401 whose fresh token is refused (token_rejected).
    forbidden = google_error(403, "PERMISSION_DENIED", NO_PERMISSION, reason="forbidden")
    replies = [ok([row("a")]), httpx.Response(401), httpx.Response(401), forbidden, httpx.Response(401)]
    fake = FakeGoogle(key.public_key(), query_replies=replies, token_replies=[None, None, oauth_error(400, "invalid_grant", "Invalid JWT")])
    async with GscClient(config, transport=fake.transport(), clock=ManualClock(NOW)) as gsc:
        shown.append(repr(gsc))
        await gsc.query(a_query())
        for _ in range(3):
            with pytest.raises(GscRequestError) as raised:
                await gsc.query(a_query())
            shown += [str(raised.value), repr(raised.value), describe_error(raised.value), raised.value.detail or ""]
    for bad in (env(key, PICK_GSC_SA_PRIVATE_KEY=as_env(pem).replace("PRIVATE KEY", "RSA PRIVATE KEY")), env(key, PICK_GSC_SA_PRIVATE_KEY=as_env(pem)[:900])):
        with pytest.raises(GscConfigError) as refused:
            load_config(bad)
        shown += [str(refused.value), repr(refused.value), describe_error(refused.value)]
    assert caplog.records, "the client logs each request; this test is empty without them"
    haystack = caplog.text + "\n".join(shown)
    for secret in (*key_lines(pem), *fake.assertions, *fake.issued):
        assert secret not in haystack


def test_gsc_client_import_is_light():
    """The gsc cron imports the client; like the rest of observe it stays off the gateway runtime (plan D1, D7)."""
    code = (
        "import importlib, json, sys\n"
        f"sys.path[:0] = {json.dumps([str(SOURCE)])}\n"
        "importlib.import_module('ggwork_pick.observe.gsc.client')\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    done = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr[-2000:]
    modules = json.loads(done.stdout.strip().splitlines()[-1])
    heavy = ("deerflow", "fastapi", "alembic", "langgraph", "langchain", "dotenv", "ggwork_pick.context", "ggwork_pick.routes", "ggwork_pick.service")
    assert [name for name in modules if any(name == prefix or name.startswith(prefix + ".") for prefix in heavy)] == []
