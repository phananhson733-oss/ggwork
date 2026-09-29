"""Official remote MCP endpoints configured from credentials alone.

The gateway owns each endpoint, so an install form can only supply credential
fields: a token is never sent anywhere but its own provider.
"""

import base64
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_TOKEN = re.compile(r"[A-Za-z0-9._~+/=-]{8,4096}")
_EMAIL = re.compile(r"[^\s@:]{1,128}@[^\s@:]{1,253}\.[^\s@:]{1,63}")


@dataclass(frozen=True)
class RemotePreset:
    url: str
    fields: tuple[str, ...]
    headers: Callable[[dict[str, str]], dict[str, str]]
    # A connection check only passes when a discovered tool name contains one of these: some servers answer
    # rejected credentials with a reduced public tool set instead of an error.
    expected_tools: tuple[str, ...] = ()
    missing_tools_hint: str | None = None


def _basic(email: str, token: str) -> str:
    return "Basic " + base64.b64encode(f"{email}:{token}".encode()).decode()


PRESETS: dict[str, RemotePreset] = {
    # Read-only toolsets; tools the token's scopes do not cover are rejected by GitHub.
    "github": RemotePreset("https://api.githubcopilot.com/mcp/readonly", ("token",), lambda c: {"Authorization": f"Bearer {c['token']}"}),
    # Rovo MCP server with a scoped API token; an org admin enables token auth first.
    "atlassian": RemotePreset(
        "https://mcp.atlassian.com/v1/mcp",
        ("email", "api_token"),
        lambda c: {"Authorization": _basic(c["email"], c["api_token"])},
        # Invalid or unscoped tokens still list the public Teamwork Graph tools.
        expected_tools=("jira", "confluence"),
        missing_tools_hint="No Jira or Confluence tools: check the account email and scoped API token, and that an org admin enabled API token authentication",
    ),
}


def preset_for_url(url: str | None) -> RemotePreset | None:
    return next((preset for preset in PRESETS.values() if preset.url == url), None)


def remote_connection(plugin_id: str, configuration: dict[str, Any]) -> dict[str, Any]:
    """Build the HTTP MCP definition for ``plugin_id`` from exactly its credential fields."""
    preset = PRESETS.get(plugin_id)
    if preset is None:
        raise ValueError("Unknown remote plugin")
    if set(configuration) != set(preset.fields):
        raise ValueError("Supply the required credentials only")
    values: dict[str, str] = {}
    for field in preset.fields:
        value = configuration[field]
        value = value.strip() if isinstance(value, str) else None
        if value is None or not (_EMAIL if field == "email" else _TOKEN).fullmatch(value):
            raise ValueError(f"Invalid credential: {field}")
        values[field] = value
    return {"type": "http", "url": preset.url, "headers": preset.headers(values), "enabled": True}
