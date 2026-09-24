"""Where a RealShort feed URL may point (security-4): https, or plain http only to this machine or a .test host.

Every feed request carries a Bearer token: the feed token (v1) or the export token (feed v2, the curve backfill). An
http:// URL to a real host would send it in cleartext before any redirect could be refused, so it is refused first.
"""

from urllib.parse import urlsplit

CLEARTEXT_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
TEST_DOMAIN = ".test"  # RFC 2606: never a real host; the tests' doubles live there
HTTPS_REQUIRED = "必须是 https：token 不走明文（只有本机与 .test 测试主机可以用 http）"


def cleartext_allowed(host: object) -> bool:
    """Whether plain http may reach host: this machine, or a name under .test."""
    name = host.lower().rstrip(".") if isinstance(host, str) else ""
    return name in CLEARTEXT_HOSTS or name.endswith(TEST_DOMAIN)


def secure_enough(url: object) -> bool:
    """https, or http to a host cleartext_allowed takes; anything unparsable or another scheme is not."""
    if not isinstance(url, str):
        return False
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and cleartext_allowed(parts.hostname))
