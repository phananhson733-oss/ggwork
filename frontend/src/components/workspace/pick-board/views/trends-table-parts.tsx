// Shared presentational styles for the simplified reference table only.
import { parseSyncTime } from "@/core/pick/sync-schema";

export const SECTION =
  "border-line bg-panel mb-4 rounded-lg border px-4 py-3 text-[13px] leading-[1.65]";
export const NOTE =
  "border-warning-line bg-warning-surface text-warning-ink mb-2 rounded-lg border px-3 py-2 text-[13px]";
export const HEADING = "text-ink-1 mb-2 text-[14px] font-semibold";
export const SUBHEADING = "text-ink-1 mt-3 mb-1.5 text-[13px] font-semibold";
export const MUTED = "text-helper";
export const LINK = "text-link hover:underline";
export const TABLE = "w-full border-collapse text-[13px]";
export const TH =
  "border-line text-helper border-b px-2 py-1.5 text-left font-normal";
export const TD = "border-line border-b px-2 py-1.5 align-top";

export function at(value: string | null | undefined): string {
  const moment = parseSyncTime(value);
  return moment
    ? `${moment.toISOString().slice(0, 16).replace("T", " ")} UTC`
    : "时间不详";
}
