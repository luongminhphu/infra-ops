import { describe, expect, it } from "vitest";
import { daysLate, splitQueue } from "./TeamQueue";
import type { Job } from "./jobs";

const job = (id: string, eff: string, assignee: { user_id: string; name: string } | null, kind = "onboard") =>
  ({ id, params: { effective_date: eff, kind }, assignee }) as unknown as Job;

describe("splitQueue", () => {
  it("queues unclaimed jobs by effective date and groups the rest per person, least loaded first", () => {
    const an = { user_id: "u1", name: "An" };
    const binh = { user_id: "u2", name: "Bình" };
    const { waiting, byPerson } = splitQueue([
      job("1", "2999-12-05", null),
      job("2", "2999-12-01", null),
      job("3", "2999-12-03", an),
      job("4", "2999-12-02", an),
      job("5", "2999-12-04", binh),
    ]);
    expect(waiting.map((j) => j.id)).toEqual(["2", "1"]);
    expect(byPerson.map((p) => [p.name, p.jobs.map((j) => j.id)])).toEqual([["Bình", ["5"]], ["An", ["4", "3"]]]);
  });

  it("puts offboards past their effective date first", () => {
    const { waiting } = splitQueue([job("on", "2000-01-01", null), job("off", "2000-02-01", null, "offboard")]);
    expect(waiting.map((j) => j.id)).toEqual(["off", "on"]);
    expect(daysLate(job("x", "2026-11-20", null), "2026-11-26")).toBe(6);
    expect(daysLate(job("x", "2026-11-27", null), "2026-11-26")).toBe(0);
  });
});
