import { utcToZonedLocalInput } from "@/core/scheduled-tasks/cron";

import {
  type Plan,
  type PlanRowInput,
  planRowInputSchema,
} from "./completion-types";

export function editablePlan(plan: Plan) {
  return {
    title: plan.title,
    timezone: plan.timezone,
    rows: plan.rows.map((row) => {
      const input = Object.fromEntries(
        Object.keys(planRowInputSchema.shape).map((key) => [
          key,
          row[key as keyof typeof row],
        ]),
      );
      return planRowInputSchema.parse(input);
    }),
  };
}
/** Enumerate timezone offsets around the date, then round-trip each candidate. */
export function localTimeChoices(
  local: string,
  timezone: string,
): { instant: string; offset: string; fold: number }[] {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(local)) return [];
  const wall = Date.parse(`${local}:00Z`);
  if (!Number.isFinite(wall)) return [];
  try {
    const offsets = new Set<number>();
    for (let h = -36; h <= 36; h += 6) {
      const probe = wall + h * 3600000;
      offsets.add(
        Date.parse(
          `${utcToZonedLocalInput(new Date(probe).toISOString(), timezone)}:00Z`,
        ) - probe,
      );
    }
    return [...offsets]
      .map((offset) => ({ ms: wall - offset, offset }))
      .filter(
        ({ ms }) =>
          utcToZonedLocalInput(new Date(ms).toISOString(), timezone) === local,
      )
      .sort((a, b) => a.ms - b.ms)
      .map(({ ms, offset }, fold) => ({
        instant: new Date(ms).toISOString(),
        fold,
        offset: `UTC${offset < 0 ? "-" : "+"}${String(Math.floor(Math.abs(offset) / 3600000)).padStart(2, "0")}:${String((Math.abs(offset) / 60000) % 60).padStart(2, "0")}`,
      }));
  } catch {
    return [];
  }
}
export function timezonePreview(
  plan: Plan,
  timezone: string,
  mode: "keep_local_time" | "keep_instant",
): PlanRowInput[] {
  new Intl.DateTimeFormat("en-US", { timeZone: timezone }).format();
  return editablePlan(plan).rows.map((row, index) => {
    const instant = plan.rows[index]?.scheduled_at;
    if (mode === "keep_local_time" || !instant) return { ...row, fold: null };
    const local = utcToZonedLocalInput(instant, timezone);
    const options = localTimeChoices(local, timezone);
    const chosen = options.find(
      (option) => Date.parse(option.instant) === Date.parse(instant),
    );
    return {
      ...row,
      local_time: local,
      fold: options.length > 1 ? (chosen?.fold ?? null) : null,
    };
  });
}
