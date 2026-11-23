import { describe, expect, it } from "vitest";
import { jobStatusLabel, type Job, type JobStep } from "./jobs";

const step = (over: Partial<JobStep>): JobStep =>
  ({ key: "a", seq: 3, kind: "auto", status: "waiting", requires_confirm: false, confirmed_at: null, ...over }) as JobStep;
const job = (s: JobStep, status: Job["status"] = "waiting") => ({ status, current_step: s.key, steps: [s] });

describe("jobStatusLabel", () => {
  it("says what a waiting job waits for and at which step", () => {
    expect(jobStatusLabel(job(step({ status: "verify" })))).toBe("Chờ Operator kiểm tra bước 3");
    expect(jobStatusLabel(job(step({ requires_confirm: true })))).toBe("Chờ nhập lại Mã NV ở bước 3");
    expect(jobStatusLabel(job(step({ kind: "manual" })))).toBe("Chờ Operator thực hiện bước 3");
    expect(jobStatusLabel(job(step({})))).toBe("Chờ hệ thống ở bước 3");
  });
  it("falls back to the plain job status", () => {
    expect(jobStatusLabel(job(step({}), "failed"))).toBe("Lỗi");
    expect(jobStatusLabel({ status: "waiting", current_step: null, steps: [] })).toBe("Chờ xử lý");
  });
});
