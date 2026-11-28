import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({
    me: { nav: [{ module: "core", label: "Core", items: [
      { label: "Trang chủ", path: "/", icon: "layout-dashboard", permission: "x" },
      { label: "Việc của tôi", path: "/me/work", icon: "user-check", permission: "x" },
    ] }] },
  }),
}));
vi.mock("./Topbar", () => ({ Topbar: () => null }));
vi.mock("@/core/lib/nextActions", () => ({
  useNextActions: () => ({
    data: { failed: [], items: [
      { key: "a", kind: "job", priority: 0, passive: false },
      { key: "b", kind: "job", priority: 1, passive: false },
      { key: "c", kind: "job", priority: 1, passive: true },
      { key: "d", kind: "notice", priority: 0, passive: false },
    ] },
  }),
}));

const { AppShell } = await import("./AppShell");

describe("AppShell rail badge", () => {
  it("shows the count of actionable queue items on the Việc của tôi entry only", () => {
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter><AppShell /></MemoryRouter>
      </QueryClientProvider>,
    );
    expect(screen.getAllByText(", 2 việc cần làm").length).toBeGreaterThan(0);
    expect(screen.queryByText(", 3 việc cần làm")).toBeNull();
  });
});
