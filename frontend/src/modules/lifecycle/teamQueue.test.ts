import { describe, expect, it } from "vitest";
import { splitQueue } from "./TeamQueue";
import type { Job } from "./jobs";

const job = (id: string, eff: string, assignee: { user_id: string; name: string } | null) =>
  ({ id, params: { effective_date: eff }, assignee }) as unknown as Job;

describe("splitQueue", () => {
  it("queues unclaimed jobs by effective date and groups the rest per person, least loaded first", () => {
    const an = { user_id: "u1", name: "An" };
    const binh = { user_id: "u2", name: "Bình" };
    const { waiting, byPerson } = splitQueue([
      job("1", "2026-12-05", null),
      job("2", "2026-12-01", null),
      job("3", "2026-12-03", an),
      job("4", "2026-12-02", an),
      job("5", "2026-12-04", binh),
    ]);
    expect(waiting.map((j) => j.id)).toEqual(["2", "1"]);
    expect(byPerson.map((p) => [p.name, p.jobs.map((j) => j.id)])).toEqual([["Bình", ["5"]], ["An", ["4", "3"]]]);
  });
});
