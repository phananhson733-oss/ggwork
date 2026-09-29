"""Live connection check for one configured MCP server.

Discovers the server's tools once with its saved definition and reports a
safe outcome. Messages carry status codes and exception types only: exception
text can contain credential-bearing URLs or headers.
"""

import asyncio
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Literal

import httpx

from deerflow.capabilities.remote import preset_for_url
from deerflow.config.extensions_config import ExtensionsConfig, McpServerConfig
from deerflow.mcp.client import build_server_params
from deerflow.mcp.headers import apply_header_overrides
from deerflow.mcp.oauth import get_initial_oauth_headers

CHECK_TIMEOUT_SECONDS = 30.0
MAX_LISTED_TOOLS = 50

CheckCode = Literal["ok", "auth_failed", "unreachable", "timeout", "no_tools", "provider_error", "error"]


@dataclass(frozen=True)
class ConnectionCheck:
    ok: bool
    code: CheckCode
    tool_count: int = 0
    tools: tuple[str, ...] = field(default_factory=tuple)
    detail: str | None = None


def _causes(error: BaseException) -> Iterator[BaseException]:
    """Every exception reachable through groups, causes and contexts, once each."""
    seen: set[int] = set()
    stack = [error]
    while stack:
        current = stack.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, BaseExceptionGroup):
            stack.extend(current.exceptions)
            continue
        yield current
        stack.extend(link for link in (current.__cause__, current.__context__) if link is not None)


def classify_failure(error: BaseException) -> ConnectionCheck:
    causes = list(_causes(error))
    for cause in causes:
        if isinstance(cause, httpx.HTTPStatusError):
            status = cause.response.status_code
            return ConnectionCheck(False, "auth_failed" if status in (401, 403) else "provider_error", detail=f"HTTP {status}")
    if any(isinstance(cause, (TimeoutError, httpx.TimeoutException)) for cause in causes):
        return ConnectionCheck(False, "timeout")
    if any(isinstance(cause, (httpx.NetworkError, ConnectionError)) for cause in causes):
        return ConnectionCheck(False, "unreachable")
    return ConnectionCheck(False, "error", detail=type(causes[0] if causes else error).__name__)


def bundled_credentials(server: McpServerConfig) -> tuple[str, dict[str, str]] | None:
    """Provider and credentials of a bundled business launcher, else None."""
    from deerflow.capabilities.business import CREDENTIALS, is_bundled_connection

    env = server.env or {}
    if (server.type or "stdio") != "stdio" or not is_bundled_connection(server.command, list(server.args), env):
        return None
    provider = server.args[3]
    return provider, {name: env[variable] for name, variable in CREDENTIALS[provider].items()}


async def check_mcp_server(name: str, server: McpServerConfig, *, timeout: float = CHECK_TIMEOUT_SECONDS) -> ConnectionCheck:
    from langchain_mcp_adapters.client import MultiServerMCPClient

    try:
        params = build_server_params(name, server)
    except ValueError:
        return ConnectionCheck(False, "error", detail="Incomplete connection definition")
    try:
        if params["transport"] in ("sse", "http"):
            oauth = await get_initial_oauth_headers(ExtensionsConfig(mcp_servers={name: server}, skills={}))
            if name in oauth:
                params["headers"] = apply_header_overrides(params.get("headers", {}), {"Authorization": oauth[name]})
        client = MultiServerMCPClient({name: params}, tool_name_prefix=False)
        tools = await asyncio.wait_for(client.get_tools(server_name=name), timeout=timeout)
    except Exception as error:  # noqa: BLE001 - every failure becomes a reported outcome
        return classify_failure(error)
    names = tuple(sorted(tool.name for tool in tools))
    if not names:
        return ConnectionCheck(False, "no_tools")
    listed = names[:MAX_LISTED_TOOLS]
    preset = preset_for_url(server.url) if server.type in ("http", "sse") else None
    if preset is not None and preset.expected_tools and not any(marker in name.lower() for name in names for marker in preset.expected_tools):
        return ConnectionCheck(False, "auth_failed", len(names), listed, preset.missing_tools_hint)
    bundled = bundled_credentials(server)
    detail = None
    if bundled is not None:
        from deerflow.capabilities.business import verify_credentials

        try:
            detail = await asyncio.wait_for(verify_credentials(*bundled), timeout=timeout)
        except ValueError as error:
            # Bundled clients raise credential-free messages by contract.
            return ConnectionCheck(False, "provider_error", len(names), listed, str(error))
        except Exception as error:  # noqa: BLE001
            return ConnectionCheck(False, "provider_error", len(names), listed, type(error).__name__)
    return ConnectionCheck(True, "ok", len(names), listed, detail)
