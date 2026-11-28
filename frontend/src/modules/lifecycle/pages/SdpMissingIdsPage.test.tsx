import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: () => true }) }));
const cand = vi.hoisted(() => ({ employee_id: "003551", biostar_user_id: "3551", biostar_name: "Nguyen Van A", source: "HCM", level: "exact", notes: [] }));
vi.mock("@/core/api/client", () => ({
  api: vi.fn().mockResolvedValue({
    run: { id: "r", finished_at: "2026-01-02T03:04:00Z" },
    sources: { sdp: "ok", biostar: "ok", failed_systems: [] },
    items: [{ sdp_user_id: "1", name: "Nguyễn Văn A", email: "a@x.vn", department: "IT", current_value: "", status: "matched", suggestion: cand, candidates: [cand] }],
    counts: { matched: 1 },
    total_missing: 1,
  }),
}));

import { SdpMissingIdsPage } from "./SdpMissingIdsPage";

describe("SdpMissingIdsPage", () => {
  it("shows the suggestion row, status label and export link", async () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter><SdpMissingIdsPage /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByText("003551")).toBeTruthy();
    expect(screen.getAllByText(/Khớp tên/).length).toBeGreaterThan(0);
    expect(screen.getByRole("link", { name: /Xuất Excel/ }).getAttribute("href")).toBe("/api/lifecycle/sdp-missing-ids.xlsx?only_candidates=true");
  });
});
