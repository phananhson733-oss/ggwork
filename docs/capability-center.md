# Capability Center integration contract

Capability Center is a discovery and configuration layer over DeerFlow's existing
MCP, Lark CLI, and skill services. It uses the existing administrator/user roles.
Agent selections configure the tools and skills assembled for a run; they are not
an authorization boundary. Customer-specific authorization can remain in the
existing policy extension points.

Skill installation discovery applies the same caller visibility policy as
`/api/skills`, including its configured fail-open or fail-closed behavior.
Public host APIs for skill evolution (mutation transactions, completed-task
snapshots, evaluation runners, lifecycle events and command registration) are
deferred to a separate proposal and PR.

## Ownership

| Concern | Owner |
| --- | --- |
| Catalog and localized metadata | `backend/packages/harness/deerflow/capabilities/builtin.json` |
| Validated manifest schema | `deerflow.capabilities.catalog.PluginManifest` |
| Installation/status adapters | `backend/app/gateway/capabilities.py` |
| Catalog and safe discovery HTTP API | `backend/app/gateway/routers/capabilities.py` |
| MCP settings, secrets, enable/delete, cache reload | Existing `/api/mcp/config` services and `extensions_config.json` |
| Lark installation and personal account authorization | Existing `/api/integrations/lark` services |
| Skill archives, enable state, user storage | Existing `/api/skills` services |
| Agent selection | Agent config `mcp_plugins` and existing `skills` |
| Integration-specific UI | `frontend/src/components/workspace/capabilities/plugin-adapters.tsx` |

There is no second credential database, background plugin daemon, executable
package loader, or remote marketplace dependency. The operator Python extension
loader is separate from this user-facing directory.

## Bundled business integrations

These entries ship working API clients with the harness, using the `business`
configuration adapter and the existing stdio MCP runtime. No separate service,
package download, or Dify runtime is needed. Configure them under **Capability
Center → Plugins** as an administrator:

| Plugin | Configuration | Tools |
| --- | --- | --- |
| Feishu group notifications (`feishu-bot`) | The custom bot's webhook token (after `/hook/`) and its signing secret; enable signature verification on the bot | `send_message`: text, or Markdown as a card, to that group |
| Feishu Docs (`feishu-docs`) | A custom app's App ID and App Secret with `docx:document:readonly` and `wiki:wiki:readonly`; add the app to each document (… → Add Document App) or wiki space | `read_document`: docx or wiki link (feishu.cn / larkoffice.com) to paged plain text |
| Google Docs (`google-docs`) | None | `read_document`: a document shared as "Anyone with the link can view", exported as Markdown |
| Exa (`exa`) | An Exa API key | `search`: up to 10 results with bounded text |
| Firecrawl (`firecrawl`) | A Firecrawl API key | `scrape`: one page's main content as paged Markdown |
| HubSpot CRM | A private app access token | `get_companies`: paginated company list; `create_contact`: create a contact by email and optional profile fields |
| DingTalk / WeCom group notifications | Hidden in the catalog; existing connections keep working | `send_message` |

The clients live in `deerflow/capabilities/business.py` (launcher, credentials,
tools) and `providers_*.py`. Google Docs follows redirects only to
`docs.google.com` and `*.googleusercontent.com` and accepts only text or Markdown
responses, so a private document produces a sharing hint instead of a sign-in
page. `verify_credentials` gives the connection check a side-effect-free live
test where the provider has one (HubSpot company read, Feishu tenant token,
Firecrawl credit usage); sending a message is never part of a check.

HubSpot needs `crm.objects.companies.read` for company queries and
`crm.objects.contacts.write` for contact creation. A read-only token can be used
when only company lookup is needed; the provider rejects unauthorized writes.
Notifications do not read chats, documents, or calendars and do not configure
DeerFlow's incoming IM channels. Existing manually configured CLI connections
are not rewritten when the catalog entry changes.

Configuration saves credentials without sending a message or creating a CRM
record. These deployment credentials are shared by runs allowed to use the
configured MCP server; they are not personal OAuth connections. Credentials
remain in the existing MCP `env` configuration and its masked admin editor.
Edit, toggle and delete the configured entry using the existing MCP controls.
An Agent selects these connections through **Plugins and skills**, just like
other MCP servers. New tool selection applies on the next run.

`deerflow.capabilities.business` implements fixed HTTPS provider endpoints,
bounded responses and timeouts, no redirect following or automatic write retries,
and validates the robot's `errcode` even on HTTP 200. Errors omit provider bodies
and credential-bearing URLs. Tool schemas never include credentials. An exact
`sys.executable -I -m deerflow.capabilities.business <provider>` launcher with
only the provider's known credential environment keys is accepted by the MCP
API; arbitrary interpreter paths/modules/flags/environment remain rejected.
The isolated interpreter ignores the working directory and Python environment
injection. If a deployment moves its Python environment, enabling an old launcher
returns a targeted error with the current interpreter path. Edit that connection's JSON
and replace only `command` with the indicated path; preserve its capability
metadata and masked credentials. This repairs the connection in place without
changing its installation ID or existing Agent selections.

The implementation is independently written against the provider contracts;
Dify's plugins informed the feature scope, not the source implementation:
[DingTalk](https://open.dingtalk.com/document/orgapp/custom-robot-access),
[WeCom](https://developer.work.weixin.qq.com/document/path/91770),
[HubSpot companies](https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/companies/guide),
[HubSpot contacts](https://developers.hubspot.com/docs/api-reference/legacy/crm/objects/contacts/create-contact).

## Add a catalog entry

Add one object to `builtin.json` and, optionally, a licensed local image under
`frontend/public/images/plugins/` (record its source in that directory). Ordinary
HTTP MCP entries reuse the `mcp` adapter and form; the gallery needs no changes.
For example:

```json
{
  "schema_version": 1,
  "id": "example-search",
  "version": "1",
  "name": {"en-US": "Example Search", "zh-CN": "示例搜索"},
  "description": {"en-US": "Search the team knowledge base."},
  "setup": {"en-US": "Supply your administrator-provided MCP endpoint."},
  "category": "knowledge",
  "kind": "mcp",
  "adapter": "mcp",
  "source": "https://example.com/docs/mcp",
  "auth_methods": ["none", "api_key"],
  "contributions": ["tools"],
  "config_schema": {
    "type": "object",
    "properties": {
      "name": {"type": "string", "title": "Connection name"},
      "url": {"type": "string", "title": "Server URL"},
      "authorization": {"type": "string", "title": "Authorization header", "format": "password"}
    },
    "required": ["name", "url"]
  }
}
```

`kind` describes the execution mechanism, `auth_methods` describes supported
account mechanisms, and `adapter` selects the integration flow. `guide` entries
only link to setup instructions; listing them never claims they are installed.
`remote` entries connect to a provider's official hosted MCP endpoint, which the
gateway owns in `deerflow/capabilities/remote.py`; their form only asks for
credentials (GitHub: a personal access token against the read-only endpoint;
Jira/Confluence: account email plus a scoped API token, sent as Basic auth).
`native` entries list `native_tools`, tool names from `config.yaml`; they show as
enabled when every listed tool is configured and cannot be installed from the UI.
`"hidden": true` keeps a manifest in the file but out of discovery and
installation, so a withdrawn integration returns by removing the flag.
The catalog version describes this manifest, not a remotely detected server
version. A catalog listing does not install, enable, or authorize anything.

The first MCP form supports HTTP endpoints and a deployment Authorization header.
The advanced JSON editor retains stdio, SSE, OAuth token configuration, and
per-user credential mappings supported by the existing MCP runtime. The presence
of `oauth` in a manifest is not a new interactive OAuth implementation. Lark
continues to use its existing personal account flow.

For a new integration protocol, implement the `CapabilityAdapter` interface,
register it once in `AdapterRegistry`, and register its settings component in
`pluginSettingsAdapters` when it requires a dedicated flow. Reuse the owning
service's authorization and validation; never add a provider branch to the gallery.

## API and state

- `GET /api/capabilities/catalog`: validated bundled manifests.
- `GET /api/capabilities/installations/{adapter}`: safe installation projections
  for `mcp`, `business`, `lark`, and `skills`; separate requests isolate integration failures.
- `POST /api/capabilities/installations`: administrator installation dispatch.
  Body: `plugin_id`, `name`, and adapter `configuration`. MCP configuration uses
  the existing server definition schema. HTTP/SSE connections require a valid
  HTTP(S) URL without embedded credentials; stdio connections require a command.
  Invalid transport configuration returns 422 before saving. Duplicate server
  names return 409.
- Existing owner APIs perform edit, enable/disable, uninstall, skill import/export,
  and account authorization. Query invalidation refreshes discovery after writes.

- `POST /api/capabilities/connections/check` (administrators): body `{name}`, a
  saved MCP server name. Discovers that server's tools once with its stored
  definition (regardless of its enabled flag) and returns `{name, ok, code,
  tool_count, tools, detail}`. `code` is one of `ok`, `auth_failed`,
  `unreachable`, `timeout`, `no_tools`, `provider_error`, `error`; `detail`
  carries only HTTP status codes, exception type names, or a bundled client's
  credential-free message. Bundled providers also run their side-effect-free
  `verify_credentials` check when one exists. Remote presets can require tool
  names (Jira/Confluence answer rejected credentials with public Teamwork Graph
  tools only). A passing check of an enabled server that the agent's MCP cache
  recorded as tool-less resets the cache, since failed discovery otherwise stays
  cached until the configuration file changes. The settings dialog runs the check
  right after saving; configured rows offer it again.

Discovery excludes MCP endpoints, commands, environment variables, headers,
OAuth secrets, and per-user credential mappings. `configured` means credentials
are present; it does not mean a connection test succeeded. `health: unknown`
is deliberate when no live check has run. Lark's existing configuration dialog
owns live account verification.

## Compatibility and execution

Catalog-installed MCP servers carry `capability: {id, plugin_id, version}` as
non-executable metadata in the existing config. Transport builders ignore it.
Configuration edits preserve installation identity, including edits by older
clients that omit metadata. Old entries get a deterministic ID from the existing
server key without rewriting files on GET. No provider identity is inferred from
a display name, including when choosing brand icons. Multiple installations of
one provider remain separate rows.

Installation IDs must be unique across all servers, including disabled entries
and legacy derived IDs. Creation, replacement and state changes reject collisions
before saving. Deletion still validates the configuration schema but permits
remaining ID collisions: administrators can remove entries one at a time, even
with multiple independent collision pairs. Other writes remain blocked until
those collisions are repaired; deletion never changes surviving connection IDs.
Old ambiguous configurations remain visible with `selectable: false` and `health: ambiguous`;
explicit Agent selections load none of the colliding connections. Remove a
conflicting entry or repair the deployment configuration before selecting it.
The inherited-all mode retains its previous behavior.

`mcp_plugins: null` (or omitted) keeps the previous behavior: all enabled MCP
servers. `[]` selects none. A list selects installation IDs. Missing or disabled
installations provide no tools; their IDs stay in Agent configuration so editing
other settings cannot silently broaden the selection. The same semantics apply
to the existing skill-name selection. A selection never enables a disabled tool
or grants access to another user's account.

The personal pick deployment (`config.pick.example.yaml`) admits, besides its
pick tools, only `web_search`/`web_fetch` and tools tagged as MCP-sourced:
`ggwork_pick.middleware` filters the model's tool list and rejects other calls,
with plugin calls on their own per-turn budget. Its RBAC tool policy is `allow:
'*'` with the host built-ins it never uses denied, because MCP tool names come
from whatever servers an administrator enables.

The lead Agent filters tools by their MCP source metadata. Ordinary delegated
and durable batch tasks carry the selection in their execution metadata. Each
Agent run gets a filtered list without altering the shared MCP tool cache.
The settings dialog omits unchanged plugin and skill selections on save, so an
unrelated edit does not overwrite a concurrent capability selection.
Existing skill policy, user-scoped MCP authentication, and tool execution guards
continue to run. Changes take effect on subsequent runs.

## Static demo

The read-only demo bundles a generated snapshot of the gateway catalog. After
editing `builtin.json`, run `cd frontend && pnpm catalog:sync`; the frontend unit
tests check that the snapshot matches the source. This keeps Docker and standalone
frontend builds independent of the backend source tree. Installation
projections come from existing same-origin MCP, Lark and Skills mock fixtures;
they do not require a running gateway. Demo projections omit credentials and
connection details, set `can_manage: false`, and do not imply live verification.
Writes still return 405 locally.

## Validation

Focused backend coverage includes real HTTP install/edit/delete against a
temporary config, role checks, secret-free discovery, identity preservation,
Agent persistence, delegated selection, and a real local stdio MCP invocation.
Browser tests cover catalog installation, Agent selection save/reopen, existing
icon editing, filtering, and desktop/mobile settings layouts. Browser fixture
screenshots show sample integrations; they are not production default settings.
