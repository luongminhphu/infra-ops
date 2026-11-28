import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const canMock = vi.hoisted(() => vi.fn());
vi.mock("@/core/api/client", () => ({ api: apiMock, errorMessage: String }));
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: canMock }) }));

import { SlaPage } from "./SlaPage";

const row = { key: "a", label: "Nhóm A", overdue: 2, due_soon: 1, open: 5 };
const body = {
  now: "2026-11-25T05:00:00Z",
  last_success_at: "2026-11-25T03:30:00Z",
  last_run_failed: false,
  due_soon_hours: 24,
  groups: { overdue: 7, due_soon: 3, on_track: 11, on_hold: 2, no_due: 4 },
  aging: [{ bucket: "0-1d", count: 1 }, { bucket: ">14d", count: 6 }],
  by: { technician: [{ ...row, key: "11", label: "Tech 11" }], group: [row], category: [row], priority: [row] },
  items: [],
};

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><SlaPage /></MemoryRouter>
    </QueryClientProvider>,
  );

describe("SlaPage", () => {
  beforeEach(() => apiMock.mockReset());

  it("shows the counts per SLA group and the technician table for view_all", async () => {
    canMock.mockReturnValue(true);
    apiMock.mockResolvedValue(body);
    renderPage();
    const cards = await screen.findByRole("list", { name: "Tóm tắt SLA" });
    expect(cards.textContent).toContain("7");
    expect(cards.textContent).toContain("Không có hạn");
    expect(screen.getByRole("region", { name: "Theo người xử lý" })).toBeTruthy();
  });

  it("hides the technician table for an operator", async () => {
    canMock.mockReturnValue(false);
    const { technician: _t, ...by } = body.by;
    apiMock.mockResolvedValue({ ...body, by });
    renderPage();
    expect(await screen.findByRole("region", { name: "Theo nhóm" })).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Theo người xử lý" })).toBeNull();
  });

  it("shows one EmptyState and no tables when SDP was never read", async () => {
    canMock.mockReturnValue(true);
    apiMock.mockResolvedValue({ ...body, last_success_at: null });
    renderPage();
    expect(await screen.findByText("Chưa có dữ liệu từ ServiceDesk Plus")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Đồng bộ dữ liệu" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Đồng bộ ngay/ })).toBeNull();
    expect(screen.queryByRole("list", { name: "Tóm tắt SLA" })).toBeNull();
    expect(screen.queryAllByRole("table")).toHaveLength(0);
  });

  it("offers a sync button (not the settings link) to someone who may only run the sync", async () => {
    canMock.mockImplementation((p: string) => p === "workload.sync.run");
    apiMock.mockResolvedValue({ ...body, last_success_at: null });
    renderPage();
    expect(await screen.findByRole("button", { name: /Đồng bộ ngay/ })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Đồng bộ dữ liệu" })).toBeNull();
  });

  it("asks for an administrator when the user can neither manage nor sync", async () => {
    canMock.mockReturnValue(false);
    apiMock.mockResolvedValue({ ...body, last_success_at: null });
    renderPage();
    expect(await screen.findByText("Nhờ Quản trị kết nối ServiceDesk Plus")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Đồng bộ ngay/ })).toBeNull();
  });

  it("warns when the last run failed and dates the header by the last success", async () => {
    canMock.mockReturnValue(true);
    apiMock.mockResolvedValue({ ...body, last_run_failed: true });
    renderPage();
    expect(await screen.findByText(/Lần đồng bộ gần nhất lỗi/)).toBeTruthy();
    expect(screen.getByText(/Đồng bộ lần cuối: 25\/11\/2026 10:30/)).toBeTruthy();
  });

  it("running a sync refreshes the whole workload cache", async () => {
    canMock.mockImplementation((p: string) => p === "workload.sync.run");
    apiMock.mockResolvedValue({ ...body, last_success_at: null });
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const spy = vi.spyOn(qc, "invalidateQueries");
    render(<QueryClientProvider client={qc}><MemoryRouter><SlaPage /></MemoryRouter></QueryClientProvider>);
    await userEvent.click(await screen.findByRole("button", { name: /Đồng bộ ngay/ }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith({ queryKey: ["workload"] }));
  });

  it("re-reads every 15 s until a first successful sync exists", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      canMock.mockReturnValue(true);
      apiMock.mockResolvedValue({ ...body, last_success_at: null });
      renderPage();
      await screen.findByText("Chưa có dữ liệu từ ServiceDesk Plus");
      const n = apiMock.mock.calls.length;
      await act(async () => { await vi.advanceTimersByTimeAsync(15_100); });
      expect(apiMock.mock.calls.length).toBeGreaterThan(n);
    } finally {
      vi.useRealTimers();
    }
  });

  it("collapses the breakdown tables when nothing is open", async () => {
    canMock.mockReturnValue(true);
    apiMock.mockResolvedValue({ ...body, groups: { overdue: 0, due_soon: 0, on_track: 0, on_hold: 0, no_due: 0 } });
    renderPage();
    expect(await screen.findByText("Không có task đang mở")).toBeTruthy();
    expect(screen.queryAllByRole("table")).toHaveLength(0);
  });
});
