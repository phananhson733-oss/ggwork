/**
 * The radar's moments on the frontend (plan TR-24, D10): stamps carry
 * microseconds and a JS Date keeps milliseconds only, so the pure rules
 * compare whole microseconds. The shared fixtures put cases one microsecond
 * past a bound (26 hours, 6 hours); read through Date they would pass.
 */
import { describe, expect, it } from "@rstest/core";

import {
  HOUR_US,
  dateMicros,
  instantMicros,
  stampMicros,
  utcDayOf,
} from "@/core/pick/obs-instants";

describe("stampMicros", () => {
  it("keeps the microseconds a Date would drop", () => {
    const a = stampMicros("2026-09-26T03:52:10.000000+00:00");
    const b = stampMicros("2026-09-26T03:52:10.000001+00:00");
    expect(b - a).toBe(1);
    expect(a).toBe(Date.UTC(2026, 8, 26, 3, 52, 10) * 1000);
  });

  it("reads Z, offsets and shorter fractions", () => {
    const utc = stampMicros("2026-09-24T03:52:00.123Z");
    expect(stampMicros("2026-09-24T11:52:00.123+08:00")).toBe(utc);
    expect(stampMicros("2026-09-24T03:52:00.123000+00:00")).toBe(utc);
    expect(stampMicros("2026-09-24T03:52:00Z")).toBe(utc - 123_000);
  });

  it("refuses anything that is not an aware ISO moment", () => {
    for (const bad of [
      "2026-09-24",
      "2026-09-24T03:52:00",
      "2026-09-24T03:52:00.1234567Z",
      "2026-02-30T00:00:00Z",
      "2026-09-24T24:00:00Z",
      "tomorrow",
    ])
      expect(() => stampMicros(bad), bad).toThrow();
  });
});

describe("dateMicros and instantMicros", () => {
  it("read a Date to the millisecond, and either input alike", () => {
    const date = new Date("2026-09-25T04:10:00.123Z");
    expect(dateMicros(date)).toBe(date.getTime() * 1000);
    expect(instantMicros(date)).toBe(dateMicros(date));
    expect(instantMicros("2026-09-25T04:10:00.123000+00:00")).toBe(
      dateMicros(date),
    );
    expect(() => dateMicros(new Date("x"))).toThrow();
  });

  it("an hour is 3.6e9 microseconds", () => {
    expect(HOUR_US).toBe(3_600_000_000);
  });
});

describe("utcDayOf", () => {
  it("names the UTC calendar day of a moment", () => {
    expect(utcDayOf(stampMicros("2026-09-25T23:59:59.999999+00:00"))).toBe(
      "2026-09-25",
    );
    expect(utcDayOf(stampMicros("2026-09-26T07:30:00.000000+08:00"))).toBe(
      "2026-09-25",
    );
  });
});
