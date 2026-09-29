"""Feishu custom-bot notifications and docx/wiki reading.

Contracts: https://open.feishu.cn/document/client-docs/bot-v3/add-custom-bot,
https://open.feishu.cn/document/server-docs/authentication-management/access-token/tenant_access_token_internal,
https://open.feishu.cn/document/server-docs/docs/wiki-v2/space-node/get_node,
https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/get and
https://open.feishu.cn/document/server-docs/docs/docs/docx-v1/document/raw_content.
"""

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any
from urllib.parse import urlsplit

from deerflow.capabilities.providers_http import Fetcher, ResponseTooLarge, check_page, page_text, short_text

FEISHU_API = "https://open.feishu.cn/open-apis"
FEISHU_DOC_DOMAINS = ("feishu.cn", "larkoffice.com")
BOT_CONTENT_LIMIT = 16_000  # UTF-8 bytes of message text
BOT_BODY_LIMIT = 20_000  # the documented request body cap is 20 KB
BOT_ERRORS = {
    19001: "the webhook token is invalid; copy the token at the end of the webhook address again",
    19021: "signature check failed; check the signing secret and enable Signature Verification on the bot",
    19022: "the bot's IP allowlist rejected this server",
    19024: "the bot's keyword filter rejected the message; include a configured keyword",
    11232: "rate limited (100 per minute, 5 per second per bot); wait before sending again",
    9499: "the request was rejected as malformed",
}

_TOKEN = re.compile(r"[A-Za-z0-9]{8,64}")
_LINK_PATH = re.compile(r"/([a-z]+)/([^/]+)/?")
_UNSUPPORTED = {"docs": "legacy Doc", "doc": "legacy Doc", "sheets": "Sheet", "sheet": "Sheet", "base": "Base", "bitable": "Base", "mindnotes": "Mindnote", "mindnote": "Mindnote", "slides": "Slides", "file": "file"}
_ACCESS = "the app cannot access this document. Open it and choose … > Add Document App to add this app, or add the app to the wiki space members, then retry"
_SCOPES = "the app lacks API permissions. Grant docx:document:readonly and wiki:wiki:readonly in the Feishu developer console and publish the app version"
_DOC_ERRORS = {
    1770032: _ACCESS,
    131006: _ACCESS,
    99991672: _SCOPES,
    99991679: _SCOPES,
    1770002: "document not found; check the link",
    131005: "wiki page not found; check the link",
    1770003: "the document has been deleted",
    1770033: "the document text exceeds Feishu's plain-text export limit",
    99991400: "rate limited by Feishu; wait a moment and retry",
    99991663: "the app token was rejected; check that the app is enabled",
    99991662: "the app is disabled or not installed in this tenant",
    99991673: "the app is disabled or not installed in this tenant",
}


def feishu_sign(timestamp: str, secret: str) -> str:
    """Custom-bot signature: HMAC-SHA256 keyed by "timestamp\\nsecret" over an empty message."""
    return base64.b64encode(hmac.new(f"{timestamp}\n{secret}".encode(), b"", hashlib.sha256).digest()).decode()


async def send_bot_message(fetcher: Fetcher, credentials: dict[str, str], content: str, message_type: str, title: str) -> dict[str, Any]:
    if message_type not in ("text", "markdown"):
        raise ValueError("message_type must be text or markdown")
    if not isinstance(content, str) or not content.strip() or len(content.encode("utf-8")) > BOT_CONTENT_LIMIT:
        raise ValueError(f"Message must contain 1–{BOT_CONTENT_LIMIT} UTF-8 bytes")
    if not isinstance(title, str) or not title.strip() or len(title) > 100:
        raise ValueError("Title must contain 1–100 characters")
    if message_type == "text":
        message: dict[str, Any] = {"msg_type": "text", "content": {"text": content}}
    else:
        header = {"title": {"tag": "plain_text", "content": title}, "template": "blue"}
        message = {"msg_type": "interactive", "card": {"header": header, "elements": [{"tag": "markdown", "content": content}]}}
    timestamp = str(int(time.time()))
    body = {"timestamp": timestamp, "sign": feishu_sign(timestamp, credentials["sign_secret"]), **message}
    payload = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    if len(payload) > BOT_BODY_LIMIT:
        raise ValueError("Message is too large once encoded; Feishu accepts at most 20 KB per request")
    url = f"{FEISHU_API}/bot/v2/hook/{credentials['webhook_token']}"
    reply = await fetcher.fetch("POST", url, unknown_outcome=True, content=payload, headers={"Content-Type": "application/json; charset=utf-8"})
    data = reply.json_object() or {}
    code = data["code"] if "code" in data else data.get("StatusCode")
    if type(code) is int and code == 0 and reply.ok:
        return {"sent": True}
    if type(code) is int and code != 0:
        raise ValueError(f"feishu-bot: {BOT_ERRORS.get(code, 'provider rejected the message')} (code {code})")
    if not reply.ok:
        raise ValueError(f"feishu-bot: HTTP {reply.status}; check the webhook token, bot settings and limits")
    raise ValueError("feishu-bot: provider did not confirm the message (code unknown); check the group before retrying")


def parse_feishu_link(link: str) -> tuple[str, str]:
    """Return ("docx" | "wiki", token) for a feishu.cn link or a bare docx document ID."""
    text = link.strip() if isinstance(link, str) else ""
    if _TOKEN.fullmatch(text):
        return "docx", text
    if not text or any(char.isspace() for char in text):
        raise ValueError("Supply a Feishu document link (https://<tenant>.feishu.cn/docx/... or /wiki/...) or a document ID")
    try:
        parts = urlsplit(text if "://" in text else "https://" + text)
        host = (parts.hostname or "").lower()
    except ValueError:
        raise ValueError("Supply a Feishu document link (https://<tenant>.feishu.cn/docx/... or /wiki/...)") from None
    if host == "larksuite.com" or host.endswith(".larksuite.com"):
        raise ValueError("Lark (larksuite.com) documents are not supported; only Feishu (feishu.cn) documents can be read")
    # Feishu serves the same documents under feishu.cn and larkoffice.com; both resolve through open.feishu.cn.
    if parts.scheme.lower() not in ("http", "https") or not any(host == domain or host.endswith("." + domain) for domain in FEISHU_DOC_DOMAINS):
        raise ValueError("Only Feishu document links on feishu.cn or larkoffice.com are supported")
    match = _LINK_PATH.fullmatch(parts.path)
    kind, token = match.groups() if match else ("", "")
    if kind in _UNSUPPORTED:
        raise ValueError(f"Feishu {_UNSUPPORTED[kind]} links are not supported yet; only docx documents and wiki pages can be read")
    if kind not in ("docx", "wiki") or not _TOKEN.fullmatch(token):
        raise ValueError("Unrecognized Feishu link; use a document link containing /docx/ or /wiki/")
    return kind, token


class FeishuDocs:
    """Reads docx text with a tenant_access_token; wiki nodes are resolved to their docx object."""

    def __init__(self, fetcher: Fetcher, credentials: dict[str, str]):
        self.fetcher = fetcher
        self.credentials = credentials

    async def tenant_token(self) -> str:
        body = {"app_id": self.credentials["app_id"], "app_secret": self.credentials["app_secret"]}
        reply = await self.fetcher.fetch("POST", f"{FEISHU_API}/auth/v3/tenant_access_token/internal", json=body)
        data = reply.json_object() or {}
        code, token = data.get("code"), data.get("tenant_access_token")
        if type(code) is int and code != 0:
            raise ValueError(f"feishu-docs: Feishu rejected the App ID or App Secret (code {code}); check them and that the app is enabled")
        if not reply.ok:
            raise ValueError(f"feishu-docs: HTTP {reply.status} while issuing the app token; try again later")
        if code != 0 or not isinstance(token, str) or not token or len(token) > 1024:
            raise ValueError("feishu-docs: Feishu did not issue an app token; check the App ID and App Secret")
        return token

    async def _get(self, token: str, path: str, **kwargs: Any) -> dict[str, Any]:
        try:
            reply = await self.fetcher.fetch("GET", FEISHU_API + path, headers={"Authorization": f"Bearer {token}"}, **kwargs)
        except ResponseTooLarge:
            raise ValueError("feishu-docs: the document is larger than the 2 MB read limit") from None
        data = reply.json_object() or {}
        code = data.get("code")
        if type(code) is int and code != 0:
            raise ValueError(f"feishu-docs: {_DOC_ERRORS.get(code, 'Feishu request failed')} (code {code})")
        if not reply.ok or code != 0 or not isinstance(data.get("data"), dict):
            raise ValueError(f"feishu-docs: Feishu returned an invalid response (HTTP {reply.status})")
        return data["data"]

    async def _resolve_wiki(self, token: str, node_token: str) -> str:
        node = (await self._get(token, "/wiki/v2/spaces/get_node", params={"token": node_token, "obj_type": "wiki"})).get("node")
        node = node if isinstance(node, dict) else {}
        obj_type, obj_token = node.get("obj_type"), node.get("obj_token")
        if obj_type != "docx":
            kind = _UNSUPPORTED.get(obj_type) if isinstance(obj_type, str) else None
            page = f"a Feishu {kind}, which is" if kind else "a page type that is"
            raise ValueError(f"This wiki page is {page} not supported yet; only docx documents can be read")
        if not isinstance(obj_token, str) or not _TOKEN.fullmatch(obj_token):
            raise ValueError("feishu-docs: Feishu returned an invalid wiki node")
        return obj_token

    async def read_document(self, link: str, offset: int, max_chars: int) -> dict[str, Any]:
        check_page(offset, max_chars)
        kind, target = parse_feishu_link(link)
        token = await self.tenant_token()
        document_id = await self._resolve_wiki(token, target) if kind == "wiki" else target
        info = (await self._get(token, f"/docx/v1/documents/{document_id}")).get("document")
        content = (await self._get(token, f"/docx/v1/documents/{document_id}/raw_content")).get("content")
        if not isinstance(content, str):
            raise ValueError("feishu-docs: Feishu returned no document text")
        title = short_text(info.get("title"), 300) if isinstance(info, dict) else None
        return {"title": title, "document_id": document_id, **page_text(content, offset, max_chars)}
