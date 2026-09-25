"""A stand-in for Google's token endpoint and searchAnalytics.query, for tests/observe/test_gsc_*.py (plan TR-06).

Nothing here reaches Google: every reply is built in the test. The token endpoint verifies each assertion's RS256
signature with the public half of the key the test generated, so a client that signs wrongly fails on the wire, not
only in a unit test of the signer.
"""

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, date, datetime
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

TOKEN_URL = "https://oauth2.googleapis.com/token"
QUERY_HOST = "searchconsole.googleapis.com"
EMAIL = "pick-obs@ggwork-test.iam.gserviceaccount.com"
SITE = "sc-domain:dramashortstv.com"
NOW = datetime(2026, 9, 25, 3, 25, tzinfo=UTC)
DAY = date(2026, 9, 24)


def new_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def pkcs8(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()


def pkcs1(key) -> str:
    return key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()).decode()


def as_env(pem: str, quote: str = '"') -> str:
    """The key as a Railway or .env variable holds it: one line, literal backslash-n, often still quoted (rs gsc-encoding.ts:13-17)."""
    return quote + pem.strip().replace("\n", "\\n") + quote


def key_lines(pem: str) -> list[str]:
    """The base64 body of a PEM, line by line: none of it may appear in a log, an error or a repr."""
    return [line for line in pem.strip().splitlines() if line and not line.startswith("-----")]


def env(key, **extra) -> dict[str, str]:
    return {"PICK_GSC_SA_EMAIL": EMAIL, "PICK_GSC_SA_PRIVATE_KEY": as_env(pkcs8(key)), "PICK_GSC_SITE_URL": SITE, **extra}


def row(*keys: str, clicks: float = 1, impressions: float = 10, ctr: float = 0.1, position: float = 4.5) -> dict:
    return {"keys": list(keys), "clicks": clicks, "impressions": impressions, "ctr": ctr, "position": position}


def ok(rows: Iterable[dict] | None = None, **extra) -> httpx.Response:
    payload = {**({"rows": list(rows)} if rows is not None else {}), "responseAggregationType": "byProperty", **extra}
    return httpx.Response(200, json=payload)


def google_error(status: int, word: str, message: str, *, reason: str | None = None, details: list | None = None, headers=None) -> httpx.Response:
    """The error envelope Google's JSON APIs answer with (error.code, message, status, errors[].reason, details[])."""
    errors = [{"message": message, "domain": "usageLimits" if status == 429 else "global", "reason": reason}] if reason else []
    error = {"code": status, "message": message, "status": word, "errors": errors, **({"details": details} if details else {})}
    return httpx.Response(status, json={"error": error}, headers=headers)


def oauth_error(status: int, word: str, description: str) -> httpx.Response:
    return httpx.Response(status, json={"error": word, "error_description": description})


class FakeGoogle:
    """Replies to the token endpoint and to searchAnalytics.query from the queues given; every request is kept."""

    def __init__(self, public_key, *, query_replies: Iterable = (), token_replies: Iterable = ()):
        self.public_key = public_key
        self.calls: tuple[httpx.Request, ...] = ()
        self.claims: tuple[dict, ...] = ()
        self.assertions: tuple[str, ...] = ()
        self.issued: tuple[str, ...] = ()
        self._query: Iterator = iter(query_replies)
        self._token: Iterator = iter(token_replies)

    @property
    def token_calls(self) -> tuple[httpx.Request, ...]:
        return tuple(call for call in self.calls if str(call.url) == TOKEN_URL)

    @property
    def query_calls(self) -> tuple[httpx.Request, ...]:
        return tuple(call for call in self.calls if call.url.host == QUERY_HOST)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls = (*self.calls, request)
        if str(request.url) == TOKEN_URL:
            return self._token_reply(request)
        assert request.url.host == QUERY_HOST, request.url
        reply = next(self._query, None)
        if reply is None:
            return ok([row("x")])
        if isinstance(reply, Exception):
            raise reply
        return reply

    def _token_reply(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.headers["content-type"] == "application/x-www-form-urlencoded"
        form = parse_qs(request.content.decode(), strict_parsing=True)
        assert set(form) == {"grant_type", "assertion"}
        assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
        (assertion,) = form["assertion"]
        options = {"verify_exp": False, "verify_iat": False, "verify_nbf": False}
        claims = jwt.decode(assertion, self.public_key, algorithms=["RS256"], audience=TOKEN_URL, options=options)
        self.claims, self.assertions = (*self.claims, claims), (*self.assertions, assertion)
        reply = next(self._token, None)
        if reply is not None:
            return reply
        token = f"ya29.test-token-{len(self.issued) + 1}"
        self.issued = (*self.issued, token)
        return httpx.Response(200, json={"access_token": token, "expires_in": 3599, "token_type": "Bearer"})


def sent_body(request: httpx.Request) -> dict:
    return json.loads(request.content)
