import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { ApiError } from "@/core/api/client";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ perms: new Set<string>(), api: vi.fn() }));
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: (p: string) => h.perms.has(p) }) }));
vi.mock("@/core/api/client", async (orig) => ({ ...(await orig<typeof import("@/core/api/client")>()), api: h.api }));

import { JobPlaybookPanel } from "./JobPlaybookPanel";

const guide = { id: "11111111-1111-1111-1111-111111111111", title: "Onboard", version: 3 };
const payload = (over: Record<string, unknown> = {}) => ({
  guide, pinned: false, can_edit: true, employee_id: "003551",
  run: {
    status: "needs_choice", manual_left: 1, path_complete: false, off_path_marks: ["zz"],
    trace: [
      { id: "a", kind: "step", title: "Chuẩn bị máy", manual: true, mark: null },
      { id: "j", kind: "job", title: "Tạo tài khoản", manual: false, mark: null, job_state: "done" },
      { id: "c", kind: "step", title: "Loại máy", manual: false, mark: null, options: [{ index: 0, label: "Laptop", to: "x" }, { index: 1, label: "PC", to: "y" }] },
    ],
  },
  ...over,
});
const ui = (status = "running", collapsible = false) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><JobPlaybookPanel jobId="job1" workflowKey="lifecycle.onboard" status={status} version={1} collapsible={collapsible} /></MemoryRouter>
    </QueryClientProvider>,
  );
const serve = (data: unknown) => h.api.mockImplementation(async (url: string) => (url.endsWith("/jobs/job1") ? data : payload()));

describe("JobPlaybookPanel", () => {
  beforeEach(() => {
    HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
    HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) { this.removeAttribute("open"); };
    h.perms = new Set(["playbook.guides.publish"]); h.api.mockReset();
  });

  it("renders nothing when the Job has no guide", async () => {
    serve({ guide: null });
    const { container } = ui();
    await waitFor(() => expect(h.api).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("shows the path, summary and manual-steps warning", async () => {
    serve(payload());
    ui();
    expect(await screen.findByText("Còn 1 bước tay chưa xong")).toBeTruthy();
    expect(screen.getByText("Hoàn tất")).toBeTruthy();
    expect(screen.getByText(/Chưa đi hết nhánh – chọn tiếp ở bước “Loại máy”/)).toBeTruthy();
    expect(screen.getByText("1 bước đã đánh dấu ở nhánh khác.")).toBeTruthy();
  });

  it("collapsible panel is closed by default with a heading and a one-line summary", async () => {
    serve(payload());
    const { container } = ui("running", true);
    expect(await screen.findByRole("heading", { name: /Playbook: Onboard/ })).toBeTruthy();
    expect(container.querySelector("details")!.hasAttribute("open")).toBe(false);
    expect(screen.getByText(/còn 1 bước tay · đang ở “Loại máy”/)).toBeTruthy();
    // The heading is a sibling of <details>, never inside <summary> (VoiceOver flattens summary content).
    const h = screen.getByRole("heading", { name: /Playbook: Onboard/ });
    expect(h.closest("summary")).toBeNull();
    expect(h.nextElementSibling?.tagName).toBe("DETAILS");
  });

  it("collapsible summary says the branch is finished when the path is complete", async () => {
    serve(payload({ run: { ...payload().run, status: "end", manual_left: 0, path_complete: true } }));
    ui("running", true);
    expect(await screen.findByText(/đã xong bước tay · đã đi hết nhánh/)).toBeTruthy();
  });

  it("draft Job shows manual steps in a neutral tone, running Job in a warning tone", async () => {
    serve(payload());
    const a = ui("draft");
    expect((await a.findByText("Còn 1 bước tay chưa xong")).closest("span")!.className).toContain("text-skipped");
    a.unmount();
    const b = ui("running");
    expect((await b.findByText("Còn 1 bước tay chưa xong")).closest("span")!.className).toContain("text-waiting");
  });

  it("ticks a manual step with the shown guide id and version", async () => {
    serve(payload());
    ui();
    fireEvent.click(await screen.findByRole("button", { name: /Đánh dấu xong – Chuẩn bị máy/ }));
    await waitFor(() => expect(h.api).toHaveBeenCalledWith("/api/playbook/jobs/job1/marks/a", { method: "PUT", json: { guide_id: guide.id, version: 3, status: "done" } }));
  });

  it("sends a branch choice", async () => {
    serve(payload());
    ui();
    fireEvent.click(await screen.findByRole("button", { name: "PC – Loại máy" }));
    await waitFor(() => expect(h.api).toHaveBeenCalledWith("/api/playbook/jobs/job1/choices/c", { method: "PUT", json: { guide_id: guide.id, version: 3, index: 1 } }));
  });

  it("requires a reason for 'Không áp dụng'", async () => {
    serve(payload());
    ui();
    fireEvent.click(await screen.findByRole("button", { name: "Không áp dụng – Chuẩn bị máy" }));
    fireEvent.click(screen.getByRole("button", { name: "Xác nhận" }));
    expect(await screen.findByText("Nhập lý do (từ 3 ký tự).")).toBeTruthy();
    expect(h.api.mock.calls.some(([, i]) => i?.method === "PUT")).toBe(false);
    fireEvent.change(screen.getByLabelText(/Lý do/), { target: { value: "Máy cũ giữ nguyên" } });
    fireEvent.click(screen.getByRole("button", { name: "Xác nhận" }));
    await waitFor(() => expect(h.api).toHaveBeenCalledWith("/api/playbook/jobs/job1/marks/a", { method: "PUT", json: { guide_id: guide.id, version: 3, status: "na", reason: "Máy cũ giữ nguyên" } }));
  });

  it("hides every write control when can_edit is false", async () => {
    serve(payload({ can_edit: false }));
    ui();
    await screen.findByText("Chuẩn bị máy");
    expect(screen.queryByRole("button", { name: /Đánh dấu xong/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /Không áp dụng/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "PC" })).toBeNull();
  });

  it("links the assignment screen from the ambiguous notice only with publish", async () => {
    serve({ guide: null, ambiguous: [guide, { ...guide, id: "2", title: "Khác" }] });
    ui();
    expect(await screen.findByRole("link", { name: /Gán Playbook cho quy trình/ })).toBeTruthy();
  });

  it("hides the assignment link without publish", async () => {
    h.perms = new Set();
    serve({ guide: null, ambiguous: [guide, { ...guide, id: "2", title: "Khác" }] });
    ui();
    await screen.findByText(/Có nhiều Playbook/);
    expect(screen.queryByRole("link", { name: /Gán Playbook/ })).toBeNull();
  });

  it("job node with options shows its status badge and the branch buttons, then the chosen branch with Đổi", async () => {
    const job = { id: "j", kind: "job", title: "Tạo TK", job_title: "Tạo tài khoản AD", manual: false, mark: null, job_state: "done", options: [{ index: 0, label: "Giữ", to: "x" }, { index: 1, label: "Xoá", to: "y" }] };
    serve(payload({ run: { status: "needs_choice", manual_left: 0, path_complete: false, off_path_marks: [], trace: [job] } }));
    ui();
    expect(await screen.findByText("Tạo tài khoản AD")).toBeTruthy();
    expect(screen.getByText("Hoàn tất")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Xoá – Tạo tài khoản AD" }));
    await waitFor(() => expect(h.api).toHaveBeenCalledWith("/api/playbook/jobs/job1/choices/j", { method: "PUT", json: { guide_id: guide.id, version: 3, index: 1 } }));
  });

  it("job node with a chosen branch shows it and Đổi", async () => {
    const job = { id: "j", kind: "job", title: "Tạo TK", manual: false, mark: null, job_state: "bogus", chosen: 0, options: [{ index: 0, label: "Giữ", to: "x" }] };
    serve(payload({ run: { status: "end", manual_left: 0, path_complete: true, off_path_marks: [], trace: [job] } }));
    ui();
    expect(await screen.findByText("Giữ")).toBeTruthy();
    expect(screen.getByText("Không rõ")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Đổi nhánh – Tạo TK" }));
    await waitFor(() => expect(h.api).toHaveBeenCalledWith("/api/playbook/jobs/job1/choices/j?guide_id=" + guide.id + "&version=3", { method: "DELETE" }));
  });

  it("does not offer again the branch taken on an earlier visit of a loop", async () => {
    const opts = [{ index: 0, label: "Lại", to: "x" }, { index: 1, label: "Xong", to: "y" }];
    const c = { id: "c", kind: "step", title: "Hỏi", manual: false, mark: null, options: opts };
    serve(payload({ run: { status: "needs_choice", manual_left: 0, path_complete: false, off_path_marks: [], trace: [{ ...c, chosen: 0 }, c] } }));
    ui();
    await screen.findByRole("button", { name: "Xong – Hỏi" });
    expect(screen.queryByRole("button", { name: "Lại – Hỏi" })).toBeNull();
  });

  it("shows an error with a retry button when the load fails with 500", async () => {
    h.api.mockRejectedValue(new ApiError(500, "server_error", "Lỗi máy chủ"));
    ui();
    expect(await screen.findByRole("button", { name: "Thử lại" })).toBeTruthy();
  });

  it("stays silent on 404", async () => {
    h.api.mockRejectedValue(new ApiError(404, "not_found", "x"));
    const { container } = ui();
    await waitFor(() => expect(h.api).toHaveBeenCalled());
    expect(container.textContent).toBe("");
  });

  it("untick sends DELETE with guide_id and version in the query", async () => {
    const t = payload().run.trace.slice();
    t[0] = { ...t[0]!, mark: { status: "done", reason: null, by: "An", at: "2026-01-02T03:04:00Z" } } as never;
    serve(payload({ run: { ...payload().run, trace: t } }));
    ui();
    fireEvent.click(await screen.findByRole("button", { name: "Bỏ đánh dấu – Chuẩn bị máy" }));
    await waitFor(() => expect(h.api).toHaveBeenCalledWith(`/api/playbook/jobs/job1/marks/a?guide_id=${guide.id}&version=3`, { method: "DELETE" }));
  });

  it("refetches on 409 playbook_changed", async () => {
    serve(payload());
    ui();
    const before = h.api.mock.calls.length;
    h.api.mockImplementation(async (url: string, init?: { method?: string }) => {
      if (init?.method === "PUT") throw new ApiError(409, "playbook_changed", "đổi");
      return url.endsWith("/jobs/job1") ? payload() : payload();
    });
    fireEvent.click(await screen.findByRole("button", { name: /Đánh dấu xong/ }));
    await waitFor(() => expect(h.api.mock.calls.filter(([, i]) => !i?.method).length).toBeGreaterThan(before));
  });
});
