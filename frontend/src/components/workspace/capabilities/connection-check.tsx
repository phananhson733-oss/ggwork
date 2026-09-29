"use client";

import {
  CircleAlertIcon,
  CircleCheckIcon,
  LoaderCircleIcon,
} from "lucide-react";
import { useCallback, useState } from "react";

import { type CapabilityCopy } from "@/core/capabilities/copy";
import { useCheckConnection } from "@/core/capabilities/hooks";
import type { ConnectionCheck } from "@/core/capabilities/types";
import { cn } from "@/lib/utils";

/** Tool names previewed after a successful check; the rest collapse into an ellipsis. */
const TOOL_PREVIEW_COUNT = 5;

export type ConnectionCheckState =
  | { status: "pending" }
  | { status: "done"; result: ConnectionCheck }
  | { status: "failed"; message: string };

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : String(error);
}

/** One localized sentence for a finished check; request failures read as a generic failure. */
export function connectionCheckSummary(
  copy: CapabilityCopy,
  state: Exclude<ConnectionCheckState, { status: "pending" }>,
) {
  if (state.status === "failed") return copy.checkCodes.error;
  const { result } = state;
  if (result.ok) return copy.checkOk.replace("{n}", String(result.tool_count));
  return copy.checkCodes[result.code] ?? copy.checkCodes.error;
}

/** The server's own note: a failure reason, or on success a credential summary. */
function checkDetail(
  state: Exclude<ConnectionCheckState, { status: "pending" }>,
) {
  return state.status === "failed" ? state.message : state.result.detail;
}

/**
 * Latest check per server name. A row keeps at most one request in flight:
 * callers disable their trigger while the row is pending.
 */
export function useConnectionChecks() {
  const { mutateAsync } = useCheckConnection();
  const [checks, setChecks] = useState<Record<string, ConnectionCheckState>>(
    {},
  );
  const settle = useCallback((name: string, next: ConnectionCheckState) => {
    setChecks((previous) => ({ ...previous, [name]: next }));
  }, []);
  const run = useCallback(
    (name: string) => {
      settle(name, { status: "pending" });
      void mutateAsync(name).then(
        (result) => settle(name, { status: "done", result }),
        (error: unknown) =>
          settle(name, { status: "failed", message: errorMessage(error) }),
      );
    },
    [mutateAsync, settle],
  );
  const clear = useCallback((name: string) => {
    setChecks((previous) =>
      Object.fromEntries(
        Object.entries(previous).filter(([key]) => key !== name),
      ),
    );
  }, []);
  return { checks, run, clear };
}

/** Compact status for a server row. */
export function ConnectionCheckLabel({
  state,
  copy,
}: {
  state: ConnectionCheckState;
  copy: CapabilityCopy;
}) {
  if (state.status === "pending")
    return (
      <span
        role="status"
        className="text-muted-foreground bg-muted/60 inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] leading-4"
      >
        <LoaderCircleIcon className="size-3 animate-spin" aria-hidden />
        {copy.checking}
      </span>
    );
  const ok = state.status === "done" && state.result.ok;
  const detail = checkDetail(state);
  return (
    <span
      role="status"
      title={detail ?? undefined}
      className={cn(
        "inline-block max-w-full min-w-0 truncate rounded px-1.5 py-0.5 text-[10px] leading-4",
        ok
          ? "bg-success-surface text-success-ink"
          : "bg-danger-surface text-danger-ink",
      )}
    >
      {connectionCheckSummary(copy, state)}
    </span>
  );
}

/** Full result shown in the settings dialog right after a save. */
export function ConnectionCheckResult({
  state,
  copy,
}: {
  state: ConnectionCheckState;
  copy: CapabilityCopy;
}) {
  if (state.status === "pending")
    return (
      <div
        role="status"
        className="text-muted-foreground flex items-center gap-2 rounded-lg border p-3 text-sm"
      >
        <LoaderCircleIcon className="size-4 animate-spin" aria-hidden />
        {copy.checking}
      </div>
    );
  if (state.status === "done" && state.result.ok) {
    const { tools, tool_count: count } = state.result;
    const preview = tools.slice(0, TOOL_PREVIEW_COUNT);
    return (
      <div
        role="status"
        className="bg-success-surface text-success-ink space-y-1.5 rounded-lg p-3 text-sm"
      >
        <p className="flex items-center gap-2 font-medium">
          <CircleCheckIcon className="size-4 shrink-0" aria-hidden />
          {connectionCheckSummary(copy, state)}
        </p>
        {preview.length > 0 && (
          <p className="font-mono text-xs break-words">
            {copy.checkTools}: {preview.join(", ")}
            {count > preview.length ? ", …" : ""}
          </p>
        )}
        {state.result.detail && (
          <p className="text-xs break-words">{state.result.detail}</p>
        )}
      </div>
    );
  }
  const detail = checkDetail(state);
  return (
    <div
      role="alert"
      className="bg-danger-surface text-danger-ink space-y-1.5 rounded-lg p-3 text-sm"
    >
      <p className="flex items-center gap-2 font-medium">
        <CircleAlertIcon className="size-4 shrink-0" aria-hidden />
        {connectionCheckSummary(copy, state)}
      </p>
      {detail && <p className="text-xs break-words">{detail}</p>}
      <p className="text-xs opacity-80">{copy.checkReconfigure}</p>
    </div>
  );
}
