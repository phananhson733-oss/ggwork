import { afterEach, beforeEach, describe, expect, it, rs } from "@rstest/core";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";

import {
  pluginSettingsAdapters,
  type PluginSettingsProps,
} from "@/components/workspace/capabilities/plugin-adapters";
import catalog from "@/core/capabilities/builtin.demo.json";
import type { PluginManifest } from "@/core/capabilities/types";

const mocks = rs.hoisted(() => ({
  fetch: rs.fn(),
  toast: rs.fn(),
}));
rs.mock("@/core/api/fetcher", () => ({ fetch: mocks.fetch }));
rs.mock("@/core/config", () => ({ getBackendBaseURL: () => "" }));
rs.mock("sonner", () => ({ toast: { success: mocks.toast } }));
rs.mock("@/core/i18n/hooks", () => ({
  useI18n: () => ({ locale: "en-US", t: { common: { loading: "Loading" } } }),
}));

function manifest(id: string) {
  const plugin = catalog.find((item) => item.id === id);
  if (!plugin) throw new Error(`Missing catalog entry ${id}`);
  return plugin as unknown as PluginManifest;
}

type Route = (body: unknown) => Response;
let routes: Record<string, Route> = {};

/** Every request body in this suite is a JSON string. */
function parseBody(init?: RequestInit): unknown {
  return typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
}

function requests() {
  return mocks.fetch.mock.calls.map(([url, init]) => ({
    url: String(url),
    method: (init as RequestInit | undefined)?.method ?? "GET",
    body: parseBody(init as RequestInit | undefined),
  }));
}

function renderSettings(id: string, props: Partial<PluginSettingsProps> = {}) {
  const plugin = manifest(id);
  const Settings = pluginSettingsAdapters[plugin.adapter]!;
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const onSaved = rs.fn();
  render(
    <QueryClientProvider client={client}>
      <Settings plugin={plugin} onSaved={onSaved} canManage {...props} />
    </QueryClientProvider>,
  );
  return { onSaved };
}

function field(key: string) {
  const element = document.getElementById(`plugin-field-${key}`);
  if (!(element instanceof HTMLInputElement))
    throw new Error(`Missing field ${key}`);
  return element;
}

function submit(name = "Save configuration") {
  fireEvent.click(screen.getByRole("button", { name }));
}

beforeEach(() => {
  routes = {
    "POST /api/capabilities/installations": () =>
      Response.json({ items: [], can_manage: true }),
    "POST /api/capabilities/connections/check": (body) =>
      Response.json({
        name: (body as { name: string }).name,
        ok: true,
        code: "ok",
        tool_count: 7,
        tools: ["a", "b", "c", "d", "e", "f", "g"],
        detail: null,
      }),
  };
  mocks.fetch.mockImplementation(async (url: string, init?: RequestInit) => {
    const key = `${init?.method ?? "GET"} ${url}`;
    const route = routes[key];
    if (!route) return Response.json({ detail: "Unmocked" }, { status: 500 });
    return route(parseBody(init));
  });
});

afterEach(() => {
  mocks.fetch.mockReset();
  mocks.toast.mockReset();
  cleanup();
});

describe("credential form payloads", () => {
  it("sends only credentials for a remote adapter", async () => {
    renderSettings("github");
    expect(document.getElementById("plugin-field-url")).toBeNull();
    fireEvent.change(field("name"), { target: { value: " team-code " } });
    fireEvent.change(field("token"), { target: { value: "fixture-token\n" } });
    submit();
    await screen.findByText("Connected. Tools found: 7");
    expect(requests()[0]).toEqual({
      url: "/api/capabilities/installations",
      method: "POST",
      body: {
        plugin_id: "github",
        name: "team-code",
        configuration: { token: "fixture-token" },
      },
    });
  });

  it("sends every non-name field for a business adapter", async () => {
    renderSettings("feishu-docs");
    fireEvent.change(field("app_id"), { target: { value: "cli_1" } });
    fireEvent.change(field("app_secret"), { target: { value: "secret" } });
    submit();
    await screen.findByText("Connected. Tools found: 7");
    expect(requests()[0]?.body).toEqual({
      plugin_id: "feishu-docs",
      name: "feishu-docs",
      configuration: { app_id: "cli_1", app_secret: "secret" },
    });
  });

  it("enables a credential-free plugin with an empty configuration", async () => {
    renderSettings("google-docs");
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
    expect(
      screen.queryByRole("button", { name: "Save configuration" }),
    ).toBeNull();
    submit("Enable");
    await screen.findByText("Connected. Tools found: 7");
    expect(requests()[0]?.body).toEqual({
      plugin_id: "google-docs",
      name: "google-docs",
      configuration: {},
    });
  });

  it("keeps the MCP URL form and localizes a malformed address", async () => {
    renderSettings("openviking");
    expect(field("url").placeholder).toBe("https://mcp.example.com/mcp");
    expect(field("authorization").placeholder).toBe("Bearer <token>");
    fireEvent.change(field("url"), { target: { value: "you@example.com" } });
    submit();
    expect((await screen.findByRole("alert")).textContent).toBe(
      "Enter an HTTP or HTTPS server URL.",
    );
    expect(mocks.fetch).not.toHaveBeenCalled();
    fireEvent.change(field("url"), {
      target: { value: "https://mcp.example.test/mcp" },
    });
    fireEvent.change(field("authorization"), {
      target: { value: "Bearer fixture" },
    });
    submit();
    await screen.findByText("Connected. Tools found: 7");
    expect(requests()[0]?.body).toMatchObject({
      plugin_id: "openviking",
      configuration: {
        enabled: true,
        type: "http",
        url: "https://mcp.example.test/mcp",
        headers: { Authorization: "Bearer fixture" },
      },
    });
  });
});

describe("autofill hardening", () => {
  it("opts every field out of browser and password-manager autofill", () => {
    renderSettings("atlassian");
    const form = document.querySelector("form");
    expect(form?.getAttribute("autocomplete")).toBe("off");
    const names = new Set<string>();
    for (const key of ["name", "email", "api_token"]) {
      const input = field(key);
      names.add(input.name);
      expect(input.name).toBe(`plugin-atlassian-${key}`);
      expect(input.getAttribute("spellcheck")).toBe("false");
      expect(input.hasAttribute("data-1p-ignore")).toBe(true);
      expect(input.getAttribute("data-lpignore")).toBe("true");
      expect(input.hasAttribute("data-bwignore")).toBe(true);
    }
    expect(names.size).toBe(3);
    expect(field("api_token").type).toBe("password");
    expect(field("api_token").autocomplete).toBe("new-password");
    expect(field("email").type).toBe("text");
    expect(field("email").autocomplete).toBe("off");
    expect(field("email").placeholder).toBe("name@company.com");
    expect(screen.getByText("Account email")).toBeDefined();
    expect(screen.getByText("API token")).toBeDefined();
  });
});

describe("connection check after save", () => {
  it("shows the server's credential summary on success", async () => {
    routes["POST /api/capabilities/connections/check"] = () =>
      Response.json({
        name: "hubspot",
        ok: true,
        code: "ok",
        tool_count: 4,
        tools: ["get_companies"],
        detail: "HubSpot token accepted; companies are readable",
      });
    renderSettings("hubspot");
    fireEvent.change(field("access_token"), { target: { value: "token" } });
    submit();
    const status = await screen.findByText("Connected. Tools found: 4");
    expect(status.closest("[role=status]")?.textContent).toContain(
      "HubSpot token accepted; companies are readable",
    );
  });

  it("toasts, keeps the dialog open, and lists the first tools", async () => {
    const { onSaved } = renderSettings("exa");
    fireEvent.change(field("api_key"), { target: { value: "key" } });
    submit();
    expect(await screen.findByText("Tools: a, b, c, d, e, …")).toBeDefined();
    expect(mocks.toast).toHaveBeenCalledWith("Configuration saved");
    expect(requests()[1]).toEqual({
      url: "/api/capabilities/connections/check",
      method: "POST",
      body: { name: "exa" },
    });
    expect(onSaved).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Done" }));
    expect(onSaved).toHaveBeenCalledTimes(1);
  });

  it("explains a failed check with its detail and the reconfigure path", async () => {
    routes["POST /api/capabilities/connections/check"] = () =>
      Response.json({
        name: "exa",
        ok: false,
        code: "auth_failed",
        tool_count: 0,
        tools: [],
        detail: "HTTP 401 from provider",
      });
    renderSettings("exa");
    fireEvent.change(field("api_key"), { target: { value: "key" } });
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(
      "Authentication failed. Check the token and its permissions.",
    );
    expect(alert.textContent).toContain("HTTP 401 from provider");
    expect(alert.textContent).toContain("delete this entry in the plugin list");
    expect(screen.getByRole("button", { name: "Done" })).toBeDefined();
  });

  it("reports a rejected check request and can run it again", async () => {
    routes["POST /api/capabilities/connections/check"] = () =>
      Response.json({ detail: "MCP server not found" }, { status: 404 });
    renderSettings("exa");
    fireEvent.change(field("api_key"), { target: { value: "key" } });
    submit();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Connection failed");
    expect(alert.textContent).toContain("MCP server not found");
    routes["POST /api/capabilities/connections/check"] = () =>
      Response.json({
        name: "exa",
        ok: true,
        code: "ok",
        tool_count: 1,
        tools: ["search"],
        detail: null,
      });
    fireEvent.click(screen.getByRole("button", { name: "Test connection" }));
    await screen.findByText("Connected. Tools found: 1");
    expect(screen.getByText("Tools: search")).toBeDefined();
  });

  it("stays on the form and runs no check when saving fails", async () => {
    routes["POST /api/capabilities/installations"] = () =>
      Response.json({ detail: "Invalid credential: api_key" }, { status: 422 });
    renderSettings("exa");
    fireEvent.change(field("api_key"), { target: { value: "bad" } });
    submit();
    await waitFor(() =>
      expect(screen.getByRole("alert").textContent).toBe(
        "Invalid credential: api_key",
      ),
    );
    expect(field("api_key").value).toBe("bad");
    expect(requests()).toHaveLength(1);
    expect(mocks.toast).not.toHaveBeenCalled();
  });
});

describe("native capabilities", () => {
  it.each([
    [
      [{ plugin_id: "web-search", installed: true }],
      "Enabled in this deployment",
    ],
    [[], "Not enabled in this deployment"],
  ])("reports deployment status without a save action", async (items, text) => {
    routes["GET /api/capabilities/installations/native"] = () =>
      Response.json({ items, can_manage: true });
    renderSettings("web-search");
    await waitFor(() =>
      expect(screen.getByRole("status").textContent).toContain(text),
    );
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryAllByRole("textbox")).toHaveLength(0);
  });
});
