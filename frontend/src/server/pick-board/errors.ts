import "server-only";

/**
 * Typed failures of the pick data board's mirror reads. A page renders the
 * first three as a notice; anything else reaches `error.tsx`.
 *
 * Messages are fixed text: never SQL, parameters, values or a connection
 * string. `sourceCode` keeps the SQLSTATE or driver code for logs only; the
 * original error is not attached, because its message can carry values.
 */

export type MirrorErrorCode =
  | "mirror_unavailable"
  | "mirror_misconfigured"
  | "mirror_version_gone"
  | "mirror_busy";

/** Why the mirror cannot be read as configured. */
export type MisconfiguredReason =
  | "url"
  | "ca"
  | "tls"
  | "auth"
  | "database"
  | "permission"
  | "current_unreadable"
  | "control_missing"
  | "control_shape"
  | "rules";

export class MirrorError extends Error {
  readonly code: MirrorErrorCode;
  readonly sourceCode: string | undefined;

  constructor(code: MirrorErrorCode, message: string, sourceCode?: string) {
    super(message);
    this.code = code;
    this.sourceCode = sourceCode;
  }
}

/** No reader connection is configured (PICK_MIRROR_READER_URL unset). */
export class MirrorUnavailable extends MirrorError {
  override readonly name = "MirrorUnavailable";

  constructor() {
    super("mirror_unavailable", "选剧资料镜像没有配置读连接");
  }
}

/** Configured, but not readable: bad URL or CA, missing grant, bad version. */
export class MirrorMisconfigured extends MirrorError {
  override readonly name = "MirrorMisconfigured";
  readonly reason: MisconfiguredReason;

  constructor(reason: MisconfiguredReason, sourceCode?: string) {
    super(
      "mirror_misconfigured",
      `选剧资料镜像的读连接配置有误（${reason}）`,
      sourceCode,
    );
    this.reason = reason;
  }
}

/** The version's schema is gone: pruned after it was superseded. */
export class MirrorVersionGone extends MirrorError {
  override readonly name = "MirrorVersionGone";

  constructor(sourceCode?: string) {
    super("mirror_version_gone", "选剧资料镜像版本已被清理", sourceCode);
  }
}

/** Timed out, cancelled, locked or out of connections: worth a retry. */
export class MirrorBusy extends MirrorError {
  override readonly name = "MirrorBusy";

  constructor(sourceCode?: string) {
    super("mirror_busy", "选剧资料镜像暂时忙，请稍后重试", sourceCode);
  }
}

export const DEADLOCK_SQLSTATE = "40P01";

const GONE = new Set(["42P01", "3F000"]);
const BUSY = new Set([
  "57014",
  "55P03",
  "53300",
  "57P01",
  "57P03",
  "ECONNRESET",
  "ETIMEDOUT",
  "EPIPE",
]);
const MISCONFIGURED = new Map<string, MisconfiguredReason>([
  ["42501", "permission"],
  ["28000", "auth"],
  ["28P01", "auth"],
  ["3D000", "database"],
]);
// Errors the pg driver raises with no code: pool checkout timeout, pg-pool's
// timeout on opening a new connection, the client-side query_timeout, and a
// connection that went away.
const DRIVER_BUSY =
  /^(timeout exceeded when trying to connect|timeout expired|Query read timeout|Connection terminated( unexpectedly| due to connection timeout)?|Client has encountered a connection error and is not queryable)$/;
// A full pooler, whatever code it sends: Supavisor answers XX000 with these.
const POOLER_FULL =
  /max client connections reached|unable to check out (process|connection) from the pool|too many (clients|connections)|remaining connection slots/i;
const TLS_CODE = /CERT|SSL|TLS|SELF_SIGNED|UNABLE_TO_(GET|VERIFY)/;

/** The SQLSTATE or Node error code, when the error carries one. */
export function errorCode(error: unknown): string | undefined {
  if (typeof error !== "object" || error === null) return undefined;
  const code = (error as { code?: unknown }).code;
  return typeof code === "string" ? code : undefined;
}

function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "";
}

/** Whether the connection itself failed, so the client must not be reused. */
export function isConnectionFailure(error: unknown): boolean {
  const code = errorCode(error);
  if (code === undefined) return DRIVER_BUSY.test(errorMessage(error));
  return code.startsWith("08") || /^E[A-Z]+$/.test(code);
}

function misconfiguredReason(
  error: unknown,
  code: string | undefined,
): MisconfiguredReason | undefined {
  if (code !== undefined) {
    const reason = MISCONFIGURED.get(code);
    if (reason) return reason;
    if (TLS_CODE.test(code)) return "tls";
  }
  return errorMessage(error).includes("does not support SSL connections")
    ? "tls"
    : undefined;
}

function isBusy(error: unknown, code: string | undefined): boolean {
  if (POOLER_FULL.test(errorMessage(error))) return true;
  if (code === undefined) return DRIVER_BUSY.test(errorMessage(error));
  return BUSY.has(code) || code.startsWith("08");
}

/** The typed error for a failed mirror read; anything unknown unchanged. */
export function translateMirrorError(error: unknown): unknown {
  if (error instanceof MirrorError) return error;
  const code = errorCode(error);
  if (code !== undefined && GONE.has(code)) return new MirrorVersionGone(code);
  const reason = misconfiguredReason(error, code);
  if (reason) return new MirrorMisconfigured(reason, code);
  if (isBusy(error, code)) return new MirrorBusy(code ?? "driver");
  return error;
}

// ---- the radar's pick_obs views (TR-24; D9) -------------------------------------------------------------------------

export type ObsErrorCode =
  | "obs_not_ready"
  | "obs_unreadable"
  | "obs_row_invalid";

/**
 * A failed read of the pick_obs views that the page shows as a notice of its
 * own: they are not the mirror, so a missing view is not a pruned version.
 * Fixed texts, like the mirror's; sourceCode is for logs only.
 */
export class ObsError extends Error {
  readonly code: ObsErrorCode;
  readonly sourceCode: string | undefined;

  constructor(code: ObsErrorCode, message: string, sourceCode?: string) {
    super(message);
    this.code = code;
    this.sourceCode = sourceCode;
  }
}

/** The views are not there: migration 0007 has not reached this database. */
export class ObsNotReady extends ObsError {
  override readonly name = "ObsNotReady";

  constructor(sourceCode?: string) {
    super("obs_not_ready", "观测数据未就绪", sourceCode);
  }
}

/** The views are there but the reader was not granted them (0007 ran before the role existed). */
export class ObsUnreadable extends ObsError {
  override readonly name = "ObsUnreadable";

  constructor(sourceCode?: string) {
    super("obs_unreadable", "观测数据不可读", sourceCode);
  }
}

/** The pick_obs views the page reads (obs-rows.ts OBS_VIEW_SCHEMAS). */
export type ObsViewName =
  | "sets"
  | "states"
  | "links"
  | "discoveries"
  | "run_status";

/**
 * A row the page cannot read: a type, an enum or a rule of the contract it
 * breaks, likely a contract version newer than this page (D29: refuse, never
 * guess). Names the view only; the row's values never reach a message.
 */
export class ObsRowInvalid extends ObsError {
  override readonly name = "ObsRowInvalid";
  readonly view: ObsViewName;

  constructor(view: ObsViewName) {
    super("obs_row_invalid", `观测数据有一行本页读不懂（${view}）`);
    this.view = view;
  }
}

/**
 * The typed error for a failed pick_obs read: a missing view or schema is
 * ObsNotReady, a missing grant ObsUnreadable; busy, auth, TLS and the rest
 * are the mirror's, since both read through the same reader pool.
 */
export function translateObsError(error: unknown): unknown {
  if (error instanceof MirrorError || error instanceof ObsError) return error;
  const code = errorCode(error);
  if (code !== undefined && GONE.has(code)) return new ObsNotReady(code);
  if (code === "42501") return new ObsUnreadable(code);
  return translateMirrorError(error);
}
