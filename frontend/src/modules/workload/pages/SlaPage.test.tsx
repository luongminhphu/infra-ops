import { render, screen } from "@testing-library/react";
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
});
