import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("@/core/api/client", () => ({ api: apiMock, errorMessage: String }));
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: () => true }) }));

import { WorkloadNextWidget } from "./Widget";

describe("WorkloadNextWidget", () => {
  it("says there is no data (no green check) when SDP was never read", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/workload/sync")) return { last_synced_at: null, last_success_at: null, last_run_failed: false, task: null };
      if (url.startsWith("/api/workload/technicians")) return { view_all: true, me: null, items: [], unassigned: { open: 0 } };
      return { total: 0, items: [] };
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><WorkloadNextWidget /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/Chưa có dữ liệu từ ServiceDesk Plus/)).toBeTruthy();
    expect(screen.queryByText(/Không có task quá hạn/)).toBeNull();
  });

  it("warns when the last sync failed although older data exists", async () => {
    apiMock.mockImplementation(async (url: string) => {
      if (url.startsWith("/api/workload/sync")) return { last_synced_at: null, last_success_at: "2026-11-25T03:30:00Z", last_run_failed: true, task: null };
      if (url.startsWith("/api/workload/technicians")) return { view_all: true, me: null, items: [], unassigned: { open: 0 } };
      return { total: 0, items: [] };
    });
    render(
      <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
        <MemoryRouter><WorkloadNextWidget /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText(/Lần đồng bộ gần nhất lỗi/)).toBeTruthy();
  });
});
