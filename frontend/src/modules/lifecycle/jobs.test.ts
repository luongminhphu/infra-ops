import { describe, expect, it } from "vitest";
import { jobStatusLabel, operatorTask, type Job, type JobStep } from "./jobs";

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

describe("operatorTask", () => {
  it("tells the operator what to do, or that nothing is needed", () => {
    expect(operatorTask(job(step({ status: "verify", title: "Gán tài sản" })))).toEqual({ act: true, text: expect.stringMatching(/^Bạn cần: kiểm tra kết quả bước 3 “Gán tài sản”/) });
    expect(operatorTask(job(step({ kind: "manual" }))).act).toBe(true);
    expect(operatorTask(job(step({}), "failed")).text).toMatch(/Chạy lại bước/);
    expect(operatorTask({ ...job(step({ status: "in_progress" }), "running"), stalled_minutes: 30 }).act).toBe(false);
    expect(operatorTask(job(step({ status: "in_progress" }), "running"))).toEqual({ act: false, text: expect.stringMatching(/^Không cần làm gì/) });
  });
});
