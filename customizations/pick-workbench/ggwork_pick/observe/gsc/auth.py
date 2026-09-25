"""The GSC service account: its credentials, its private key and the signed assertion (design 5.1; plan TR-06).

Copied from RealShort (rs = realshort-pick-export-v2 816ca2e): the key normalization and the PKCS#8-only check of
rs:src/lib/gsc-encoding.ts:11-45, the assertion's claims of rs:src/lib/gsc-api.ts:45-86 (iss is the service account,
no user is impersonated), and the wording of a missing variable. Not copied: ingestion, windows and merging.

Credentials come from the environment only, and only the gsc service holds them (design 3.4): PICK_GSC_SA_EMAIL with
PICK_GSC_SA_PRIVATE_KEY, or on this machine PICK_GSC_SA_FILE naming the service account's JSON key with mode 600 (U1).
The private key and the assertion never reach a log, an exception or a repr: messages name the variable, never a value.
Nothing here makes a request; the token exchange is gsc/client.py's.
"""

import json
import os
import re
import stat
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import jwt
from cryptography.exceptions import UnsupportedAlgorithm
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ggwork_pick.observe.errors import Refused

TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"
JWT_BEARER_GRANT = "urn:ietf:params:oauth:grant-type:jwt-bearer"
# Google takes an assertion valid for at most an hour; the token it returns lasts an hour too (rs gsc-api.ts:70-71).
ASSERTION_SECONDS = 3600

EMAIL_ENV = "PICK_GSC_SA_EMAIL"
KEY_ENV = "PICK_GSC_SA_PRIVATE_KEY"
FILE_ENV = "PICK_GSC_SA_FILE"
MAX_KEY_FILE_BYTES = 64 * 1024  # a service account's JSON key is about 2.3 KB

_QUOTES = re.compile(r"""^["']|["']\Z""")  # one quote off each end, as rs gsc-encoding.ts:20 does
_PKCS8_HEADER = "-----BEGIN PRIVATE KEY-----"
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,190}\.[A-Za-z]{2,24}$")
_MISSING = (
    f"缺少 {{names}}。{EMAIL_ENV} 与 {KEY_ENV} 只放在 gsc 服务上（Railway 的服务变量，前端与 gateway 都不设）；"
    f"本机改用 {FILE_ENV} 指向 600 权限的服务账号 JSON 密钥"
)


class GscConfigError(Refused):
    """The GSC credentials or property are missing or unusable: refused before any request (exit 2)."""


def normalize_private_key(raw: str) -> str:
    """The key as a variable holds it back to PEM (rs gsc-encoding.ts:19-22).

    Both fixes are needed, or loading only says the data is invalid: a service account JSON holds real newlines, which
    become a literal backslash-n once pasted into a one-line variable; and a paste from a .env often brings the quotes
    at both ends along."""
    return _QUOTES.sub("", raw.strip()).replace("\\n", "\n")


def load_private_key(raw: str, *, source: str) -> rsa.RSAPrivateKey:
    """The RSA key of a service account, from PKCS#8 PEM only (rs gsc-encoding.ts:31-45). `source` names where it came
    from, for the message; the key itself is never quoted."""
    text = normalize_private_key(raw)
    if _PKCS8_HEADER not in text:
        # A PKCS#1 key fails to load with the same message as a damaged one; that is half an hour lost (rs :27-30).
        raise GscConfigError(
            f"{source} 不是 PKCS#8 格式。service account 的 JSON 里那一份就是，它以 `{_PKCS8_HEADER}` 开头；"
            "`BEGIN RSA PRIVATE KEY` 是 PKCS#1，`BEGIN ENCRYPTED PRIVATE KEY` 带口令，都不认"
        )
    try:
        key = serialization.load_pem_private_key(text.encode("ascii"), password=None)
    except (ValueError, TypeError, UnicodeEncodeError, UnsupportedAlgorithm):
        raise GscConfigError(f"{source} 解不开：PEM 损坏，或 base64 里混进了别的字符（换行没还原？）") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise GscConfigError(f"{source} 不是 RSA 私钥：服务账号的密钥是 RSA，签名用 RS256")
    return key


@dataclass(frozen=True, eq=False)
class ServiceAccount:
    """A service account's email and RSA key. The key never shows in repr."""

    email: str
    key: rsa.RSAPrivateKey = field(repr=False)

    def sign_assertion(self, now: datetime) -> str:
        """The RS256 assertion traded for an access token (rs gsc-api.ts:62-86): iss is the account itself, no `sub`.

        `now` is the injected clock's; a naive time names no instant and is refused."""
        if now.tzinfo is None or now.utcoffset() is None:
            raise ValueError("sign_assertion needs a timezone-aware time")
        issued = int(now.timestamp())
        claims = {"iss": self.email, "scope": SCOPE, "aud": TOKEN_ENDPOINT, "iat": issued, "exp": issued + ASSERTION_SECONDS}
        return jwt.encode(claims, self.key, algorithm="RS256", headers={"typ": "JWT"})


def token_request_form(assertion: str) -> dict[str, str]:
    """The form posted to TOKEN_ENDPOINT (rs gsc-api.ts:88-95)."""
    return {"grant_type": JWT_BEARER_GRANT, "assertion": assertion}


def _email(value: object, source: str) -> str:
    text = value.strip() if isinstance(value, str) else ""
    if not _EMAIL.fullmatch(text):
        raise GscConfigError(f"{source} 不是服务账号的邮箱（形如 name@project.iam.gserviceaccount.com）")
    return text


def _read_key_file(path: Path) -> bytes:
    """The file's bytes, after checking on the open file itself that nobody but its owner can read or write it."""
    try:
        descriptor = os.open(path, os.O_RDONLY)
    except FileNotFoundError:
        raise GscConfigError(f"{FILE_ENV} 指向的文件不存在") from None
    except OSError:
        raise GscConfigError(f"{FILE_ENV} 指向的文件打不开") from None
    with os.fdopen(descriptor, "rb") as handle:
        status = os.fstat(handle.fileno())
        if not stat.S_ISREG(status.st_mode):
            raise GscConfigError(f"{FILE_ENV} 指向的不是普通文件")
        if status.st_mode & 0o077:
            raise GscConfigError(f"{FILE_ENV} 的权限宽于 600（组或其他用户可读写），先 chmod 600 再用")
        content = handle.read(MAX_KEY_FILE_BYTES + 1)
    if len(content) > MAX_KEY_FILE_BYTES:
        raise GscConfigError(f"{FILE_ENV} 超过 {MAX_KEY_FILE_BYTES} 字节，不是服务账号的 JSON 密钥")
    return content


def _from_file(path: Path) -> ServiceAccount:
    """The JSON key GCP hands out for a service account (U1). Only client_email and private_key are used: token_uri
    and the other fields are ignored, so a file cannot send the assertion anywhere but TOKEN_ENDPOINT."""
    content = _read_key_file(path)
    try:
        document = json.loads(content)
    except (ValueError, RecursionError):
        document = None
    fields = document if isinstance(document, dict) else {}
    if fields.get("type") != "service_account" or not isinstance(fields.get("client_email"), str) or not isinstance(fields.get("private_key"), str):
        raise GscConfigError(f"{FILE_ENV} 不是服务账号的 JSON 密钥（要有 type=service_account、client_email、private_key）")
    source = f"{FILE_ENV} 里的 private_key"
    return ServiceAccount(email=_email(fields["client_email"], f"{FILE_ENV} 里的 client_email"), key=load_private_key(fields["private_key"], source=source))


def load_service_account(environ: Mapping[str, str]) -> ServiceAccount:
    """The service account from one of the two sources; both set at once is refused rather than guessed between."""
    email, key, file = (environ.get(name, "").strip() for name in (EMAIL_ENV, KEY_ENV, FILE_ENV))
    if file and (email or key):
        raise GscConfigError(f"只设一种凭据：{FILE_ENV}，或 {EMAIL_ENV} 加 {KEY_ENV}")
    if file:
        return _from_file(Path(file).expanduser())
    missing = [name for name, value in ((EMAIL_ENV, email), (KEY_ENV, key)) if not value]
    if missing:
        raise GscConfigError(_MISSING.format(names=" / ".join(missing)))
    return ServiceAccount(email=_email(email, EMAIL_ENV), key=load_private_key(key, source=KEY_ENV))
