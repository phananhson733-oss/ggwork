/**
 * Moments for the radar's pure rules on the frontend (plan TR-24, D10): the
 * TS twin of ggwork_pick/observe/instants.py.
 *
 * Stored stamps carry microseconds (repository.stamp()), and a JS Date keeps
 * milliseconds only: 26 hours and one microsecond would read as exactly 26
 * hours. So the rules compare whole microseconds since the epoch, which a
 * double holds exactly for any date this page will see. `now` is always an
 * explicit input, a request's Date or a fixture's stamp; nothing here reads
 * the clock.
 */

export type Micros = number;

export const HOUR_US: Micros = 3_600_000_000;

const STAMP =
  /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?(Z|[+-]\d{2}:\d{2})$/;

function part(match: RegExpExecArray, index: number): number {
  return Number(match[index] ?? "0");
}

function offsetMinutes(zone: string): number {
  if (zone === "Z") return 0;
  const sign = zone.startsWith("-") ? -1 : 1;
  const [hours, minutes] = zone.slice(1).split(":").map(Number);
  return sign * ((hours ?? 0) * 60 + (minutes ?? 0));
}

/** An aware ISO moment in microseconds since the epoch; anything else throws. */
export function stampMicros(value: string): Micros {
  const match = STAMP.exec(value);
  if (!match) throw new Error(`not an aware ISO moment: ${value}`);
  const [year, month, day, hour, minute, second] = [1, 2, 3, 4, 5, 6].map((i) =>
    part(match, i),
  ) as [number, number, number, number, number, number];
  const millis = Date.UTC(year, month - 1, day, hour, minute, second);
  const check = new Date(millis);
  const valid =
    check.getUTCFullYear() === year &&
    check.getUTCMonth() === month - 1 &&
    check.getUTCDate() === day &&
    check.getUTCHours() === hour &&
    check.getUTCMinutes() === minute;
  if (!valid) throw new Error(`not a calendar moment: ${value}`);
  const fraction = Number((match[7] ?? "").padEnd(6, "0"));
  const zone = offsetMinutes(match[8] ?? "Z");
  return (millis - zone * 60_000) * 1000 + fraction;
}

/** A Date, to its millisecond, in microseconds; an invalid Date throws. */
export function dateMicros(value: Date): Micros {
  const millis = value.getTime();
  if (Number.isNaN(millis)) throw new Error("not a valid Date");
  return millis * 1000;
}

export function instantMicros(value: string | Date): Micros {
  return typeof value === "string" ? stampMicros(value) : dateMicros(value);
}

/** The UTC calendar day (YYYY-MM-DD) a moment falls on. */
export function utcDayOf(micros: Micros): string {
  return new Date(Math.floor(micros / 1000)).toISOString().slice(0, 10);
}
