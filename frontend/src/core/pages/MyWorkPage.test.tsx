import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: () => true }) }));
vi.mock("@/core/modules", () => ({ useModules: () => [] }));
vi.mock("@/core/api/client", () => ({ api: () => new Promise(() => {}) }));
vi.mock("@/core/lib/nextActions", async (orig) => ({
  ...(await orig<typeof import("@/core/lib/nextActions")>()),
  useNextActions: () => ({
    isLoading: false,
    isError: false,
    data: { failed: [], items: [
      { key: "jobs:unclaimed", kind: "notice", priority: 4, title: "Có Job đang chờ nhận", detail: "4 Job chưa có người phụ trách", href: "/", action: "Xem hàng đợi" },
    ] },
  }),
}));

const { MyWorkPage } = await import("./MyWorkPage");

describe("MyWorkPage", () => {
  it("does not claim there is nothing to do while a Job waits to be claimed", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter><MyWorkPage /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getByText("Bạn chưa nhận việc nào")).toBeTruthy();
    expect(screen.getByText("Có Job đang chờ nhận")).toBeTruthy();
    expect(screen.queryByText("Không còn việc nào cần bạn làm")).toBeNull();
    expect(screen.queryByRole("alert")).toBeNull(); // priority 4 = information
  });
});
