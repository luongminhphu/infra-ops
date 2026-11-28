import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
vi.mock("@/core/api/client", () => ({ api: apiMock, errorMessage: String }));
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: () => true }) }));

import { JobsPage } from "./JobsPage";

const wrap = (url = "/") => (
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <MemoryRouter initialEntries={[url]}><JobsPage /></MemoryRouter>
  </QueryClientProvider>
);

describe("JobsPage", () => {
  it("shows an error state with retry when the query fails", async () => {
    apiMock.mockRejectedValue(new Error("boom"));
    render(wrap());
    expect(await screen.findByRole("button", { name: /Thử lại/ })).toBeTruthy();
  });
  it("shows the truncation notice and reads the search from the URL", async () => {
    apiMock.mockResolvedValue({ total: 900, counts: {}, items: [] });
    render(wrap("/?q=0035"));
    expect(await screen.findByText(/Đang hiện/)).toBeTruthy();
    expect((screen.getByRole("searchbox") as HTMLInputElement).value).toBe("0035");
  });
});
