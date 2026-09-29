"use client";

import { CircleCheckIcon, CircleMinusIcon } from "lucide-react";
import dynamic from "next/dynamic";
import type { ComponentType } from "react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { type CapabilityCopy, capabilityCopy } from "@/core/capabilities/copy";
import {
  useCapabilityInstallations,
  useCheckConnection,
  useInstallCapability,
} from "@/core/capabilities/hooks";
import type { PluginManifest } from "@/core/capabilities/types";
import { useI18n } from "@/core/i18n/hooks";

import {
  ConnectionCheckResult,
  type ConnectionCheckState,
} from "./connection-check";
import { catalogText } from "./plugin-catalog";

export type PluginSettingsProps = {
  plugin: PluginManifest;
  onSaved: () => void;
  canManage: boolean;
};

/** Credential-only adapters: the backend owns the connection, the form sends the fields. */
const CREDENTIAL_ADAPTERS = new Set(["business", "remote"]);

/** Format hints only; never values a browser could mistake for saved account data. */
const FIELD_PLACEHOLDERS: Record<string, string> = {
  url: "https://mcp.example.com/mcp",
  authorization: "Bearer <token>",
  email: "name@company.com",
};

function fieldLabel(copy: CapabilityCopy, key: string, title?: string) {
  const fields: Record<string, string> = copy.fields;
  return Object.hasOwn(fields, key) ? fields[key] : (title ?? key);
}

function mcpConfiguration(
  plugin: PluginManifest,
  fields: Record<string, string>,
  locale: string,
  copy: CapabilityCopy,
) {
  let url: URL;
  try {
    url = new URL(fields.url?.trim() ?? "");
  } catch {
    throw new Error(copy.invalidUrl);
  }
  if (
    !["https:", "http:"].includes(url.protocol) ||
    url.username ||
    url.password
  )
    throw new Error(copy.invalidUrl);
  const authorization = fields.authorization?.trim();
  return {
    enabled: true,
    type: "http",
    url: url.toString(),
    description: catalogText(plugin.description, locale),
    ...(authorization ? { headers: { Authorization: authorization } } : {}),
  };
}

function buildConfiguration(
  plugin: PluginManifest,
  fields: Record<string, string>,
  locale: string,
  copy: CapabilityCopy,
): Record<string, unknown> {
  if (!CREDENTIAL_ADAPTERS.has(plugin.adapter))
    return mcpConfiguration(plugin, fields, locale, copy);
  // Pasted credentials often carry a trailing newline that no provider accepts.
  return Object.fromEntries(
    Object.entries(fields)
      .filter(([key]) => key !== "name")
      .map(([key, value]) => [key, value.trim()]),
  );
}

function ConfiguredPluginSettings({
  plugin,
  onSaved,
  canManage,
}: PluginSettingsProps) {
  const { locale, t } = useI18n();
  const copy = capabilityCopy(locale);
  const install = useInstallCapability();
  const check = useCheckConnection();
  const [fields, setFields] = useState<Record<string, string>>({
    name: plugin.id,
  });
  const [error, setError] = useState<string | null>(null);
  const [savedName, setSavedName] = useState<string | null>(null);
  const properties = Object.entries(plugin.config_schema.properties ?? {});
  // A plugin without credentials (e.g. public Google Docs) is just switched on
  // under its catalog id.
  const credentialFree = properties.every(([key]) => key === "name");
  const visible = credentialFree ? [] : properties;

  async function save(event: React.FormEvent) {
    event.preventDefault();
    setError(null);
    const name = fields.name?.trim() ?? "";
    try {
      await install.mutateAsync({
        plugin_id: plugin.id,
        name,
        configuration: buildConfiguration(plugin, fields, locale, copy),
      });
    } catch (error) {
      setError(error instanceof Error ? error.message : String(error));
      return;
    }
    toast.success(copy.saved);
    setSavedName(name);
    check.mutate(name);
  }

  if (savedName !== null) {
    const state: ConnectionCheckState = check.isSuccess
      ? { status: "done", result: check.data }
      : check.isError
        ? { status: "failed", message: check.error.message }
        : { status: "pending" };
    return (
      <div className="space-y-4">
        <ConnectionCheckResult state={state} copy={copy} />
        <div className="flex justify-end gap-2">
          <Button
            variant="outline"
            disabled={check.isPending}
            onClick={() => check.mutate(savedName)}
          >
            {copy.checkConnection}
          </Button>
          <Button onClick={onSaved}>{copy.done}</Button>
        </div>
      </div>
    );
  }

  return (
    <form
      className="space-y-4"
      autoComplete="off"
      onSubmit={(event) => void save(event)}
    >
      <p className="text-muted-foreground text-sm leading-6">
        {catalogText(plugin.setup, locale)}
      </p>
      {visible.map(([key, schema]) => {
        const secret = schema.format === "password";
        return (
          <div key={key} className="space-y-1.5">
            <label
              htmlFor={`plugin-field-${key}`}
              className="text-sm font-medium"
            >
              {fieldLabel(copy, key, schema.title)}
            </label>
            {/* Chrome fills the account email and a saved password into any
                text/password pair; unique names plus the password-manager
                opt-outs keep deployment credentials from being overwritten. */}
            <Input
              id={`plugin-field-${key}`}
              name={`plugin-${plugin.id}-${key}`}
              type={secret ? "password" : "text"}
              autoComplete={secret ? "new-password" : "off"}
              spellCheck={false}
              data-1p-ignore
              data-lpignore="true"
              data-bwignore
              placeholder={FIELD_PLACEHOLDERS[key]}
              required={plugin.config_schema.required?.includes(key)}
              value={fields[key] ?? ""}
              disabled={!canManage || install.isPending}
              onChange={(event) =>
                setFields((previous) => ({
                  ...previous,
                  [key]: event.target.value,
                }))
              }
            />
          </div>
        );
      })}
      {!credentialFree && (
        <p className="text-muted-foreground text-xs leading-5">
          {copy.accountHint}
        </p>
      )}
      {error && (
        <p role="alert" className="text-destructive text-sm">
          {error}
        </p>
      )}
      <Button type="submit" disabled={!canManage || install.isPending}>
        {install.isPending
          ? t.common.loading
          : credentialFree
            ? copy.enable
            : copy.save}
      </Button>
    </form>
  );
}

/** Built-in tools are switched on by deployment config; this view only reports it. */
function NativePluginSettings({ plugin }: PluginSettingsProps) {
  const { locale, t } = useI18n();
  const copy = capabilityCopy(locale);
  const installations = useCapabilityInstallations("native");
  const enabled = installations.data?.items.some(
    (item) => item.plugin_id === plugin.id && item.installed,
  );
  let status: string;
  if (installations.isLoading) status = t.common.loading;
  else if (installations.isError) status = copy.adapterError;
  else status = enabled ? copy.nativeEnabled : copy.nativeDisabled;
  const StatusIcon = enabled ? CircleCheckIcon : CircleMinusIcon;
  return (
    <div className="space-y-3">
      <p className="text-muted-foreground text-sm leading-6">
        {catalogText(plugin.setup, locale)}
      </p>
      <p
        role="status"
        className={
          enabled
            ? "text-success-ink flex items-center gap-2 text-sm"
            : "text-muted-foreground flex items-center gap-2 text-sm"
        }
      >
        {!installations.isLoading && (
          <StatusIcon className="size-4 shrink-0" aria-hidden />
        )}
        {status}
      </p>
    </div>
  );
}

const LarkSettings = dynamic(() =>
  import("./lark-plugin-settings").then((module) => module.LarkPluginSettings),
);
/** One registration per integration flow, never one conditional per catalog item. */
export const pluginSettingsAdapters: Record<
  string,
  ComponentType<PluginSettingsProps>
> = {
  mcp: ConfiguredPluginSettings,
  business: ConfiguredPluginSettings,
  remote: ConfiguredPluginSettings,
  native: NativePluginSettings,
  lark: () => <LarkSettings />,
};
