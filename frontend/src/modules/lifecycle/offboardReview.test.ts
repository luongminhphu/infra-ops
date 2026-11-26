import { describe, expect, it } from "vitest";
import { recentMonths } from "./pages/OffboardReviewPage";

describe("recentMonths", () => {
  it("lists 12 months back across the year boundary, last month second", () => {
    const m = recentMonths(new Date(2026, 0, 15));
    expect(m.slice(0, 3)).toEqual(["2026-01", "2025-12", "2025-11"]);
    expect(m).toHaveLength(12);
  });
});
