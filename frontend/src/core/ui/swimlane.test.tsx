import { describe, expect, it } from "vitest";
import { laneLayout, type LaneHandoff } from "./swimlane";

const steps = ["a", "b", "c", "d"].map((key) => ({ key }));
const h = (to: string, step: string | null, at: string, from: string | null = null): LaneHandoff => ({ from_name: from, to_name: to, step_key: step, source: "sdp", reason: "", at });

describe("laneLayout", () => {
  it("puts every step in the lane of whoever owned the job at that step", () => {
    const r = laneLayout(steps, [h("An", null, "2026-11-01T00:00:00Z"), h("Bình", "c", "2026-11-03T00:00:00Z", "An")]);
    expect(r.owners).toEqual(["An", "An", "Bình", "Bình"]);
    expect(r.lanes).toEqual(["An", "Bình"]);
    expect(r.changes.map((c) => c?.to_name ?? null)).toEqual([null, null, "Bình", null]);
  });
  it("handles no assignee and a hand-back", () => {
    expect(laneLayout(steps, []).owners).toEqual(Array(4).fill("Chưa có người phụ trách"));
    const r = laneLayout(steps, [h("An", null, "1"), h("Bình", "b", "2"), h("An", "d", "3")]);
    expect(r.owners).toEqual(["An", "Bình", "Bình", "An"]);
    expect(r.lanes).toEqual(["An", "Bình"]);
  });
});
