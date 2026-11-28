import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: (p: string) => p === "lifecycle.job.execute", me: { nav: [] } }) }));
vi.mock("sonner", () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import { JobWizard } from "./JobWizard";
import type { Job } from "../jobs";

const base = {
  id: "job1", workflow_key: "lifecycle.onboard", workflow_title: "Onboard", subject_label: "An", subject_key: "003551", status: "running",
  current_step: "s1", params: {}, external_ref: "1", steps: [{ key: "s1", seq: 1, title: "Bước một", kind: "manual", status: "waiting", writes: [], checklist: [] }],
  progress: { done: 0, total: 1 }, assignee: null, assignments: [], events: [],
};
const asJob = (o: object) => ({ ...base, ...o }) as unknown as Job;

const calls: string[] = [];
function mockApi(assignment: object, claim?: { status: number; body: object }) {
  vi.stubGlobal("fetch", vi.fn(async (url: string, init?: RequestInit) => {
    calls.push(`${init?.method ?? "GET"} ${url}`);
    const isClaim = url.endsWith("/claim");
    const r = isClaim ? claim ?? { status: 201, body: {} } : { status: 200, body: assignment };
    return new Response(JSON.stringify(r.body), { status: r.status, headers: { "content-type": "application/json" } });
  }));
}
const wizard = (j: Job) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter><JobWizard job={j} onFull={() => {}} /></MemoryRouter>
  </QueryClientProvider>,
);
afterEach(() => { vi.unstubAllGlobals(); calls.length = 0; });

describe("Nhận Job in the wizard", () => {
  it("unassigned job offers Nhận Job and posts the claim", async () => {
    mockApi({ pending: false });
    wizard(asJob({}));
    await userEvent.click(await screen.findByRole("button", { name: /Nhận Job/ }));
    await waitFor(() => expect(calls).toContain("POST /api/lifecycle/jobs/job1/claim"));
  });
  it("pending assignment shows the status line instead of the button", async () => {
    mockApi({ pending: true, id: "a1", status: "running", to_name: "Tôi", mine: true });
    wizard(asJob({}));
    expect(await screen.findByText("Đang ghi bạn là người phụ trách lên SDP…")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Nhận Job/ })).toBeNull();
  });
  it("hand-over to someone else says who gets it, not 'you'", async () => {
    mockApi({ pending: true, id: "a1", status: "running", to_name: "Lan", mine: false });
    wizard(asJob({}));
    expect(await screen.findByText("Đang chuyển cho Lan…")).toBeTruthy();
    expect(screen.queryByText(/ghi bạn/)).toBeNull();
  });
  it("waiting for the requester's key names them and polls slowly", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockApi({ pending: true, id: "a1", status: "waiting", to_name: "Lan", mine: false, waiting_reason: "personal_key", requested_by_name: "Sup" });
      wizard(asJob({}));
      expect(await screen.findByText("Đang chờ khoá API của Sup")).toBeTruthy();
      const n = () => calls.filter((c) => c.endsWith("/assignment")).length;
      const before = n();
      await act(async () => { await vi.advanceTimersByTimeAsync(5000); });
      expect(n()).toBe(before); // 4 s polling would have fired by now
      await act(async () => { await vi.advanceTimersByTimeAsync(26_000); });
      expect(n()).toBeGreaterThan(before);
    } finally {
      vi.useRealTimers();
    }
  });
  it("a failed hand-over is not announced as an alert and the section is labelled by its heading", async () => {
    mockApi({ pending: false, id: "a1", status: "failed", message: "Lỗi cũ" });
    wizard(asJob({}));
    expect(await screen.findByText("Lỗi cũ")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByRole("region", { name: "Job này chưa có người phụ trách" })).toBeTruthy();
  });
  it("missing personal key shows a link to Khoá API của tôi", async () => {
    mockApi({ pending: false }, { status: 409, body: { error: { code: "personal_credential_missing", message: "x" } } });
    wizard(asJob({}));
    await userEvent.click(await screen.findByRole("button", { name: /Nhận Job/ }));
    expect((await screen.findByRole("link", { name: "Khoá API của tôi" })).getAttribute("href")).toBe("/me/credentials");
  });
  it("assigned job shows Phụ trách and no claim button", async () => {
    mockApi({ pending: false });
    wizard(asJob({ assignee: { user_id: "u1", name: "Lan" } }));
    expect(await screen.findByText("Lan")).toBeTruthy();
    expect(screen.getByText(/Phụ trách:/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Nhận Job/ })).toBeNull();
  });
  it("name-only assignee: no button, says who has it on SDP", async () => {
    mockApi({ pending: false });
    wizard(asJob({ assignee: { user_id: null, name: "Tech 70" } }));
    expect(await screen.findByText(/phụ trách trên SDP/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Nhận Job/ })).toBeNull();
  });
});
