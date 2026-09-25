"""The Trends cookie jar and the user agent bound to it (plan TR-04; design 4.4; D18, D23).

One jar per service. It is warmed at most once per target date (D23: the 20:30 and 00:10 halves of one session are one
day), and a rate-limited session keeps its jar, since Google refuses new sessions first: nothing here throws a jar away.
The user agent belongs to the jar. A jar is created with one and never changes it, and request_headers() hands out
the two together, so the jar's cookies never go out under another user agent; a new user agent means a fresh, empty
jar. The jar serves Trends' own hosts only, so cookies are not matched by domain.

Cookie values are secret. repr, str and every error name cookies and never show a value; the jar reaches storage only
sealed (seal_jar: a Fernet token for the runtime row's Text column, D18) or inside the encrypted state file.
"""

import json
import logging
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime
from types import MappingProxyType

from ggwork_pick.observe.crypto import StateCipher
from ggwork_pick.observe.errors import StateUnavailable

logger = logging.getLogger(__name__)

MAX_USER_AGENT = 512
_TOKEN_EXCLUDED = frozenset('()<>@,;:\\"/[]?={} \t')  # RFC 9110 token delimiters
_VALUE_EXCLUDED = frozenset('",;\\')  # RFC 6265 cookie-octet leaves these out
_DOMAIN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
_COOKIE_FIELDS = frozenset({"name", "value", "domain", "path", "expires"})
_JAR_FIELDS = frozenset({"user_agent", "warmed_on", "cookies"})


def _visible_ascii(text: str) -> bool:
    return all(33 <= ord(char) <= 126 for char in text)


def _is_token(text: str) -> bool:
    return bool(text) and _visible_ascii(text) and not _TOKEN_EXCLUDED.intersection(text)


def _is_cookie_value(text: str) -> bool:
    return _visible_ascii(text) and not _VALUE_EXCLUDED.intersection(text)


def _is_user_agent(text: str) -> bool:
    return 0 < len(text) <= MAX_USER_AGENT and all(32 <= ord(char) <= 126 for char in text)


def _require_aware(now: datetime) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now 须带时区")


@dataclass(frozen=True, repr=False)
class Cookie:
    """One cookie. expires is Unix seconds, as http.cookiejar keeps it; None lasts as long as the jar."""

    name: str
    value: str
    domain: str = ""
    path: str = "/"
    expires: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _is_token(self.name):
            raise ValueError("cookie 名须是非空的 HTTP token")
        if not isinstance(self.value, str) or not _is_cookie_value(self.value):
            raise ValueError(f"cookie {self.name} 的值含有不能进请求头的字符")
        if not isinstance(self.domain, str) or not _DOMAIN_CHARS.issuperset(self.domain):
            raise ValueError(f"cookie {self.name} 的 domain 不合法")
        if not isinstance(self.path, str) or not self.path.startswith("/") or not _is_cookie_value(self.path):
            raise ValueError(f"cookie {self.name} 的 path 不合法")
        if self.expires is not None and (type(self.expires) is not int):
            raise ValueError(f"cookie {self.name} 的 expires 须是整数秒")

    @property
    def key(self) -> tuple[str, str, str]:
        """A Set-Cookie with the same name, domain and path replaces this cookie."""
        return (self.name, self.domain, self.path)

    def expired(self, now: datetime) -> bool:
        _require_aware(now)
        return self.expires is not None and now.timestamp() >= self.expires

    def to_document(self) -> dict:
        return {"name": self.name, "value": self.value, "domain": self.domain, "path": self.path, "expires": self.expires}

    @classmethod
    def from_document(cls, document: object) -> "Cookie":
        if not isinstance(document, Mapping) or set(document) != _COOKIE_FIELDS:
            raise ValueError("cookie 的字段不对")
        return cls(**document)

    def __repr__(self) -> str:
        return f"Cookie(name={self.name!r}, domain={self.domain!r}, path={self.path!r}, value=<redacted>)"


def _merged(existing: Iterable[Cookie], incoming: Iterable[Cookie]) -> tuple[Cookie, ...]:
    by_key = {cookie.key: cookie for cookie in existing} | {cookie.key: cookie for cookie in incoming}
    return tuple(by_key.values())


@dataclass(frozen=True, repr=False)
class CookieJar:
    user_agent: str
    cookies: tuple[Cookie, ...] = ()
    warmed_on: date | None = None  # the target date of the last warm-up (D23)

    def __post_init__(self) -> None:
        if not isinstance(self.user_agent, str) or not _is_user_agent(self.user_agent):
            raise ValueError(f"UA 须是 1–{MAX_USER_AGENT} 个可打印 ASCII 字符")
        cookies = tuple(self.cookies)
        if not all(isinstance(cookie, Cookie) for cookie in cookies):
            raise ValueError("cookies 须全是 Cookie")
        if len({cookie.key for cookie in cookies}) != len(cookies):
            raise ValueError("同名、同 domain、同 path 的 cookie 只能有一个")
        if self.warmed_on is not None and (type(self.warmed_on) is not date):
            raise ValueError("warmed_on 须是日期")
        object.__setattr__(self, "cookies", cookies)

    @classmethod
    def fresh(cls, user_agent: str) -> "CookieJar":
        """An empty jar for this user agent; the first warm-up fills it."""
        return cls(user_agent)

    def can_warm(self, day: date) -> bool:
        return self.warmed_on is None or self.warmed_on < day

    def warmed(self, cookies: Iterable[Cookie], *, day: date) -> "CookieJar":
        """The jar after the warm-up of target date `day`; a second warm-up that day is refused."""
        if not self.can_warm(day):
            raise ValueError("这个罐在该目标日已经预热过（每个目标日最多一次）")
        return replace(self, cookies=_merged(self.cookies, cookies), warmed_on=day)

    def updated(self, cookies: Iterable[Cookie]) -> "CookieJar":
        """The jar after an ordinary response's Set-Cookie headers; the user agent stays."""
        return replace(self, cookies=_merged(self.cookies, cookies))

    def request_headers(self, now: datetime) -> Mapping[str, str]:
        """The User-Agent and Cookie headers, always together; expired cookies are left out."""
        live = tuple(cookie for cookie in self.cookies if not cookie.expired(now))
        cookie_header = {"Cookie": "; ".join(f"{cookie.name}={cookie.value}" for cookie in live)} if live else {}
        return MappingProxyType({"User-Agent": self.user_agent, **cookie_header})

    def to_document(self) -> dict:
        """The jar as JSON data. It carries the cookie values, so it is only ever stored sealed."""
        warmed_on = f"{self.warmed_on:%Y-%m-%d}" if self.warmed_on is not None else None  # a target date, not a stamp
        return {"user_agent": self.user_agent, "warmed_on": warmed_on, "cookies": [cookie.to_document() for cookie in self.cookies]}

    @classmethod
    def from_document(cls, document: object) -> "CookieJar":
        if not isinstance(document, Mapping) or set(document) != _JAR_FIELDS:
            raise ValueError("cookie 罐的字段不对")
        warmed_on, cookies = document["warmed_on"], document["cookies"]
        if not isinstance(cookies, list) or not (warmed_on is None or isinstance(warmed_on, str)):
            raise ValueError("cookie 罐的字段类型不对")
        day = date.fromisoformat(warmed_on) if warmed_on is not None else None
        return cls(document["user_agent"], tuple(Cookie.from_document(cookie) for cookie in cookies), day)

    def __repr__(self) -> str:
        names = [cookie.name for cookie in self.cookies]
        return f"CookieJar(user_agent={self.user_agent!r}, cookies={names!r}, warmed_on={self.warmed_on})"


def seal_jar(cipher: StateCipher, jar: CookieJar) -> str:
    """The jar, user agent included, as a Fernet token: ASCII text for the runtime row's Text column (D18)."""
    plain = json.dumps(jar.to_document(), sort_keys=True, separators=(",", ":")).encode("ascii")
    return cipher.seal(plain).decode("ascii")


def open_jar(cipher: StateCipher, token: str, *, user_agent: str | None = None) -> CookieJar:
    """The jar sealed in `token`. With user_agent (the row's plain UA column), the two must agree: a jar and its user
    agent are replaced together or not at all. StateUnavailable on any failure: the day does not run on a guessed jar."""
    try:
        opened = cipher.open(token.encode("ascii"))
    except UnicodeEncodeError:
        raise StateUnavailable("cookie 罐的密文不是 ASCII 文本") from None
    try:
        jar = CookieJar.from_document(json.loads(opened.data))
    except (TypeError, ValueError):  # json and date parsing errors are ValueErrors
        raise StateUnavailable("cookie 罐解开后格式不对") from None
    if user_agent is not None and jar.user_agent != user_agent:
        raise StateUnavailable("UA 列与 cookie 罐里封存的 UA 不一致：罐与 UA 必须成对更换")
    if opened.key_index:
        logger.warning("[pick-obs] the cookie jar was sealed with key #%d of %d; reseal it with key #1", opened.key_index + 1, cipher.key_count)
    return jar
