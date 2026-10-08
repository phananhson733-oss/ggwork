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
  | "mirror_busy"
  | "mirror_period_missing"
  | "mirror_session_required";

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

export class MirrorSessionRequired extends MirrorError {
  override readonly name = "MirrorSessionRequired";
  constructor() {
    super("mirror_session_required", "登录验证需要更新");
  }
}

export class MirrorPeriodMissing extends MirrorError {
  override readonly name = "MirrorPeriodMissing";
  constructor() {
    super("mirror_period_missing", "该榜单没有可读取的期次");
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
