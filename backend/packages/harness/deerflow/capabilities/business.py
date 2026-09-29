"""Bundled business tools served through the existing stdio MCP lifecycle.

These clients implement the documented provider APIs independently. Credentials
stay in MCP process environment, never in tool arguments or discovery results.
Provider protocols live in ``providers_feishu`` and ``providers_web``; this module
owns the launcher contract, credential validation and MCP tool registration.
"""

import argparse
import base64
import hashlib
import hmac
import os
import re
import sys
import time
from collections.abc import Callable
from typing import Annotated, Any, Literal

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from pydantic import Field

from deerflow.capabilities.providers_feishu import FeishuDocs, send_bot_message
from deerflow.capabilities.providers_http import MAX_PAGE_CHARS, Fetcher, expect_json
from deerflow.capabilities.providers_web import exa_search, firecrawl_credits, firecrawl_scrape, read_google_doc

MODULE = "deerflow.capabilities.business"
CREDENTIALS = {
    "dingtalk": {"access_token": "DEERFLOW_DINGTALK_ACCESS_TOKEN", "sign_secret": "DEERFLOW_DINGTALK_SIGN_SECRET"},
    "wecom": {"webhook_key": "DEERFLOW_WECOM_WEBHOOK_KEY"},
    "hubspot": {"access_token": "DEERFLOW_HUBSPOT_ACCESS_TOKEN"},
    "feishu-bot": {"webhook_token": "DEERFLOW_FEISHU_BOT_TOKEN", "sign_secret": "DEERFLOW_FEISHU_BOT_SECRET"},
    "feishu-docs": {"app_id": "DEERFLOW_FEISHU_APP_ID", "app_secret": "DEERFLOW_FEISHU_APP_SECRET"},
    "google-docs": {},
    "exa": {"api_key": "DEERFLOW_EXA_API_KEY"},
    "firecrawl": {"api_key": "DEERFLOW_FIRECRAWL_API_KEY"},
}
_CREDENTIAL = re.compile(r"[A-Za-z0-9_.~+/=-]{1,4096}")
# Credentials interpolated into a URL path must not be able to add path segments.
_PATH_CREDENTIAL = re.compile(r"[A-Za-z0-9_-]{1,256}")
_PATH_FIELDS = {("feishu-bot", "webhook_token")}


def connection_config(provider: str, configuration: dict[str, Any]) -> dict[str, Any]:
    fields = CREDENTIALS.get(provider)
    if fields is None or set(configuration) != set(fields):
        raise ValueError("Supply the required credentials only")
    env = {}
    for field, variable in fields.items():
        value = configuration[field]
        pattern = _PATH_CREDENTIAL if (provider, field) in _PATH_FIELDS else _CREDENTIAL
        if not isinstance(value, str) or not pattern.fullmatch(value):
            raise ValueError(f"Invalid credential: {field}")
        env[variable] = value
    return {"type": "stdio", "command": sys.executable, "args": ["-I", "-m", MODULE, provider], "env": env, "enabled": True}


def is_bundled_connection(command: str | None, args: list[str], env: dict[str, str]) -> bool:
    """Narrow exception to the executable allowlist, including edits/toggles.

    Never trust catalog metadata to grant execution. The interpreter, module,
    provider, flags and allowed environment keys must all match our own launcher.
    """
    return command == sys.executable and len(args) == 4 and args[:3] == ["-I", "-m", MODULE] and args[3] in CREDENTIALS and set(env) == set(CREDENTIALS[args[3]].values())


class BusinessClient:
    def __init__(self, provider: str, credentials: dict[str, str], http: httpx.AsyncClient | None = None):
        connection_config(provider, credentials)
        self.provider = provider
        self.credentials = credentials
        self.http = http
        self.fetcher = Fetcher(provider, http)

    async def _request(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        # A failed write may still have been delivered; reads can simply be repeated.
        reply = await self.fetcher.fetch(method, url, unknown_outcome=method != "GET", **kwargs)
        if not reply.ok:
            raise ValueError(f"{self.provider}: HTTP {reply.status}; check credentials, permissions and provider limits")
        return expect_json(reply)

    def _require(self, provider: str, tool: str) -> None:
        if self.provider != provider:
            raise ValueError(f"This provider has no {tool} tool")

    async def send_message(self, content: str, message_type: Literal["text", "markdown"] = "text", title: str = "Notification") -> dict[str, Any]:
        if self.provider == "feishu-bot":
            return await send_bot_message(self.fetcher, self.credentials, content, message_type, title)
        if self.provider not in ("dingtalk", "wecom"):
            raise ValueError("This provider has no group notification tool")
        limit = 2048 if message_type == "text" or self.provider == "dingtalk" else 4096
        if message_type not in ("text", "markdown") or not content.strip() or len(content.encode("utf-8")) > limit:
            raise ValueError(f"Message must contain 1–{limit} UTF-8 bytes")
        if not title.strip() or len(title) > 100:
            raise ValueError("Title must contain 1–100 characters")
        if self.provider == "dingtalk":
            timestamp = str(int(time.time() * 1000))
            secret = self.credentials["sign_secret"]
            signature = hmac.new(secret.encode(), f"{timestamp}\n{secret}".encode(), hashlib.sha256).digest()
            params = {"access_token": self.credentials["access_token"], "timestamp": timestamp, "sign": base64.b64encode(signature).decode()}
            url = "https://oapi.dingtalk.com/robot/send"
            body = {"text": content, "title": title} if message_type == "markdown" else {"content": content}
        else:
            params = {"key": self.credentials["webhook_key"]}
            url = "https://qyapi.weixin.qq.com/cgi-bin/webhook/send"
            body = {"content": content}
        data = await self._request("POST", url, params=params, json={"msgtype": message_type, message_type: body})
        code = data.get("errcode")
        if type(code) is not int or code != 0:
            safe_code = str(code) if type(code) is int else "unknown"
            raise ValueError(f"{self.provider}: provider rejected the message (code {safe_code}); check robot settings and limits")
        return {"sent": True}

    async def read_document(self, link: str, offset: int = 0, max_chars: int = 20_000) -> dict[str, Any]:
        if self.provider == "feishu-docs":
            return await FeishuDocs(self.fetcher, self.credentials).read_document(link, offset, max_chars)
        self._require("google-docs", "document")
        return await read_google_doc(self.fetcher, link, offset, max_chars)

    async def search(self, query: str, num_results: int = 5) -> list[dict[str, Any]]:
        self._require("exa", "search")
        return await exa_search(self.fetcher, self.credentials["api_key"], query, num_results)

    async def scrape(self, url: str, offset: int = 0, max_chars: int = 20_000) -> dict[str, Any]:
        self._require("firecrawl", "scrape")
        return await firecrawl_scrape(self.fetcher, self.credentials["api_key"], url, offset, max_chars)

    async def get_companies(self, limit: int = 10, after: str | None = None) -> dict[str, Any]:
        if not 1 <= limit <= 100 or (after is not None and len(after) > 512):
            raise ValueError("Invalid page size or cursor")
        params = {"limit": str(limit), "properties": "name,domain,industry,phone,city,country", "archived": "false"}
        if after:
            params["after"] = after
        data = await self._hubspot("GET", "/crm/v3/objects/companies", params=params)
        if not isinstance(data.get("results"), list):
            raise ValueError("HubSpot returned an invalid company list")
        return {"companies": data["results"], "next_after": data.get("paging", {}).get("next", {}).get("after")}

    async def create_contact(self, email: str, firstname: str = "", lastname: str = "", phone: str = "", company: str = "", jobtitle: str = "") -> dict[str, Any]:
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email) or len(email) > 254:
            raise ValueError("Supply a valid contact email")
        fields = {"email": email, "firstname": firstname, "lastname": lastname, "phone": phone, "company": company, "jobtitle": jobtitle}
        if any(len(value) > 1000 for value in fields.values()):
            raise ValueError("Contact fields must not exceed 1000 characters")
        data = await self._hubspot("POST", "/crm/v3/objects/contacts", json={"properties": {key: value for key, value in fields.items() if value}})
        if not data.get("id"):
            raise ValueError("HubSpot did not return a created contact ID")
        return {"id": data["id"], "properties": data.get("properties", {})}

    async def _hubspot(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        if self.provider != "hubspot":
            raise ValueError("This provider has no CRM tools")
        return await self._request(method, "https://api.hubapi.com" + path, headers={"Authorization": f"Bearer {self.credentials['access_token']}"}, **kwargs)


async def verify_credentials(provider: str, credentials: dict[str, str], http: httpx.AsyncClient | None = None) -> str | None:
    """Cheap live credential check for the connection-check endpoint.

    Returns a short English summary on success and raises ValueError with a safe
    message on failure. Returns None when no free, side-effect-free check exists:
    a bot check would post to the group and a search would spend quota.
    """
    client = BusinessClient(provider, credentials, http)
    if provider == "hubspot":
        await client.get_companies(1)
        return "HubSpot token accepted; companies are readable"
    if provider == "feishu-docs":
        await FeishuDocs(client.fetcher, credentials).tenant_token()
        return "Feishu app token issued"
    if provider == "firecrawl":
        return await firecrawl_credits(client.fetcher, credentials["api_key"])
    return None


READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)
Offset = Annotated[int, Field(ge=0, description="Character offset; pass next_offset from the previous result")]
MaxChars = Annotated[int, Field(ge=1, le=MAX_PAGE_CHARS, description="Maximum characters to return")]
DOCUMENT_TOOLS = {
    "feishu-docs": (
        "Read the plain text of a Feishu docx document or wiki page. Pass its link (https://<tenant>.feishu.cn/docx/... or /wiki/...) "
        "or a document_id from an earlier result. Long documents are paged: call again with offset=next_offset. "
        "Sheets, Bases and legacy Docs are not supported."
    ),
    "google-docs": (
        'Read a Google Docs document shared as "Anyone with the link can view". Pass its docs.google.com/document/d/... link '
        "or a document_id from an earlier result. Returns Markdown when available; page long documents with offset=next_offset."
    ),
}
ClientFactory = Callable[[], BusinessClient]


def _hubspot_tools(server: FastMCP, client: ClientFactory) -> None:
    @server.tool(annotations=READ_ONLY)
    async def get_companies(limit: Annotated[int, Field(ge=1, le=100)] = 10, after: str | None = None) -> dict[str, Any]:
        """Read a page of HubSpot companies; pass next_after to retrieve the next page."""
        return await client().get_companies(limit, after)

    @server.tool(annotations=WRITE)
    async def create_contact(email: str, firstname: str = "", lastname: str = "", phone: str = "", company: str = "", jobtitle: str = "") -> dict[str, Any]:
        """Create a real HubSpot contact when requested. Do not retry an uncertain write without checking for duplicates."""
        return await client().create_contact(email, firstname, lastname, phone, company, jobtitle)


def _robot_tools(server: FastMCP, client: ClientFactory) -> None:
    @server.tool(annotations=WRITE)
    async def send_message(content: str, message_type: Literal["text", "markdown"] = "text", title: str = "Notification") -> dict[str, Any]:
        """Send a real notification to the configured group robot when requested. Does not read chats. Do not automatically retry uncertain delivery."""
        return await client().send_message(content, message_type, title)


def _document_tools(server: FastMCP, client: ClientFactory, description: str) -> None:
    @server.tool(annotations=READ_ONLY, description=description)
    async def read_document(link: Annotated[str, Field(min_length=1, max_length=2048)], offset: Offset = 0, max_chars: MaxChars = 20_000) -> dict[str, Any]:
        return await client().read_document(link, offset, max_chars)


def _exa_tools(server: FastMCP, client: ClientFactory) -> None:
    @server.tool(annotations=READ_ONLY)
    async def search(query: Annotated[str, Field(min_length=1, max_length=2000)], num_results: Annotated[int, Field(ge=1, le=10)] = 5) -> list[dict[str, Any]]:
        """Search the web with Exa. Returns up to num_results sources with title, url, published_date and a text excerpt. Each call uses the account's Exa quota."""
        return await client().search(query, num_results)


def _firecrawl_tools(server: FastMCP, client: ClientFactory) -> None:
    @server.tool(annotations=READ_ONLY)
    async def scrape(url: Annotated[str, Field(min_length=1, max_length=2048)], offset: Offset = 0, max_chars: MaxChars = 20_000) -> dict[str, Any]:
        """Fetch a public web page through Firecrawl and return its main content as Markdown. Long pages are paged with offset=next_offset; every call scrapes again and uses Firecrawl credits."""
        return await client().scrape(url, offset, max_chars)


def build_server(provider: str) -> FastMCP:
    if provider not in CREDENTIALS:
        raise ValueError("Unknown business provider")
    server = FastMCP(f"deerflow-{provider}", log_level="WARNING")

    def client() -> BusinessClient:
        return BusinessClient(provider, {field: os.environ.get(variable, "") for field, variable in CREDENTIALS[provider].items()})

    if provider == "hubspot":
        _hubspot_tools(server, client)
    elif provider in DOCUMENT_TOOLS:
        _document_tools(server, client, DOCUMENT_TOOLS[provider])
    elif provider == "exa":
        _exa_tools(server, client)
    elif provider == "firecrawl":
        _firecrawl_tools(server, client)
    else:
        _robot_tools(server, client)
    return server


def main() -> None:
    parser = argparse.ArgumentParser(description="DeerFlow bundled business MCP tools")
    parser.add_argument("provider", choices=CREDENTIALS)
    build_server(parser.parse_args().provider).run()


if __name__ == "__main__":
    main()
