import { describe, expect, it } from "vitest";
import { bucketLabel, presetRange, todayVn } from "./periods";

describe("presetRange", () => {
  it("computes calendar periods ending today", () => {
    // 2026-11-23 is a Monday; 2026-11-26 a Thursday.
    expect(presetRange("week", "2026-11-26")).toEqual({ from: "2026-11-23", to: "2026-11-26" });
    expect(presetRange("week", "2026-11-23")).toEqual({ from: "2026-11-23", to: "2026-11-23" });
    expect(presetRange("week", "2026-11-29")).toEqual({ from: "2026-11-23", to: "2026-11-29" });
    expect(presetRange("month", "2026-11-26").from).toBe("2026-11-01");
    expect(presetRange("quarter", "2026-11-26").from).toBe("2026-10-01");
    expect(presetRange("half", "2026-03-10").from).toBe("2025-10-01");
    expect(presetRange("year", "2026-11-26").from).toBe("2025-12-01");
  });
  it("uses Vietnam time for today", () => {
    expect(todayVn(new Date("2026-11-23T18:30:00Z"))).toBe("2026-11-24");
  });
  it("labels buckets", () => {
    expect(bucketLabel("2026-11-02", "week")).toBe("02/11");
    expect(bucketLabel("2026-11-01", "month")).toBe("11/2026");
  });
});
