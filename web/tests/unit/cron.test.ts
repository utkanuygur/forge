import { describe, expect, it } from "vitest";
import { CronError, describeCron, nextTimes, parseCron, PRESETS, validTimeZone } from "../../src/lib/cron";

const iso = (ms: number) => new Date(ms).toISOString().slice(0, 16);
const at = (s: string) => Date.parse(s);

describe("cron preview (TRG8)", () => {
  it("describes presets and common shapes in plain words", () => {
    expect(describeCron("0 9 * * 1-5")).toBe("Every weekday at 9:00");
    expect(describeCron("0 * * * *")).toBe("Every hour");
    expect(describeCron("@hourly")).toBe("Every hour");
    expect(describeCron("15 * * * *")).toBe("Every hour at minute 15");
    expect(describeCron("*/15 * * * *")).toBe("Every 15 minutes");
    expect(describeCron("* * * * *")).toBe("Every minute");
    expect(describeCron("30 18 * * *")).toBe("Every day at 18:30");
    expect(describeCron("0 9 * * mon,fri")).toBe("Every Monday and Friday at 9:00");
    expect(describeCron("0 10 * * 0,6")).toBe("Every weekend day at 10:00");
    expect(describeCron("0 8 1 * *")).toBe("On day 1 of every month at 8:00");
    expect(describeCron("0 8 1 6 *")).toBe("Custom schedule");
    expect(describeCron("61 * * * *")).toBe("Invalid schedule");
    for (const p of PRESETS) expect(describeCron(p.cron)).toBe(p.label);
  });

  it("rejects malformed expressions with readable errors", () => {
    for (const bad of ["", "* * * *", "60 * * * *", "* * * 13 *", "*/0 * * * *", "5-1 * * * *", "0 0 30 2 *"]) {
      expect(() => parseCron(bad)).toThrow(CronError);
    }
    expect(() => parseCron("* * * *")).toThrow(/5 fields/);
  });

  it("computes the next three weekday fire times in the chosen time zone", () => {
    const from = at("2026-10-09T10:00:00Z"); // Friday
    expect(nextTimes("0 9 * * 1-5", "UTC", from).map(iso)).toEqual([
      "2026-10-12T09:00",
      "2026-10-13T09:00",
      "2026-10-14T09:00",
    ]);
    // 9:00 in Berlin is 07:00 UTC in summer time and 08:00 UTC after it ends on 2026-10-25
    expect(nextTimes("0 9 * * *", "Europe/Berlin", at("2026-10-24T00:00:00Z")).map(iso)).toEqual([
      "2026-10-24T07:00",
      "2026-10-25T08:00",
      "2026-10-26T08:00",
    ]);
  });

  it("matches the server's daylight saving rules", () => {
    // spring forward: 02:30 does not exist on 2026-03-08 in New York, so it runs at 03:30 EDT (07:30 UTC)
    expect(nextTimes("30 2 * * *", "America/New_York", at("2026-03-07T17:00:00Z"), 2).map(iso)).toEqual([
      "2026-03-08T07:30",
      "2026-03-09T06:30",
    ]);
    // fall back: a fixed time fires once, an hour wildcard fires in both repeated hours
    expect(nextTimes("30 1 * * *", "America/New_York", at("2026-10-31T16:00:00Z"), 2).map(iso)).toEqual([
      "2026-11-01T05:30",
      "2026-11-02T06:30",
    ]);
    expect(nextTimes("30 * * * *", "America/New_York", at("2026-11-01T04:45:00Z")).map(iso)).toEqual([
      "2026-11-01T05:30",
      "2026-11-01T06:30",
      "2026-11-01T07:30",
    ]);
  });

  it("handles day of month or weekday, month ends and leap years", () => {
    expect(nextTimes("0 0 13 * 5", "UTC", at("2026-10-10T00:00:00Z"), 2).map(iso)).toEqual([
      "2026-10-13T00:00",
      "2026-10-16T00:00",
    ]);
    expect(nextTimes("0 0 31 * *", "UTC", at("2026-09-01T00:00:00Z"), 1).map(iso)).toEqual(["2026-10-31T00:00"]);
    expect(nextTimes("0 0 29 2 *", "UTC", at("2026-03-01T00:00:00Z"), 1).map(iso)).toEqual(["2028-02-29T00:00"]);
  });

  it("validates time zone names", () => {
    expect(validTimeZone("America/New_York")).toBe(true);
    expect(validTimeZone("Mars/Olympus")).toBe(false);
  });
});
