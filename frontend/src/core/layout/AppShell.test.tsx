import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { useState } from "react";

vi.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({
    me: { nav: [{ module: "core", label: "Core", items: [
      { label: "Trang chủ", path: "/", icon: "layout-dashboard", permission: "x" },
      { label: "Việc của tôi", path: "/me/work", icon: "user-check", permission: "x" },
    ] }, { module: "m", label: "Mod", items: [
      { label: "A", path: "/m/a", icon: "layout-grid", permission: "x", group: "G" },
      { label: "B", path: "/m/b", icon: "layout-grid", permission: "x", group: "G" },
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
const { useFocusLayout } = await import("./focus");
import { Route, Routes } from "react-router-dom";

function Page({ highlight }: { highlight?: string }) {
  useFocusLayout({ highlight });
  return <h1>Trang</h1>;
}
function Harness({ highlight }: { highlight?: string }) {
  const [on, setOn] = useState(true);
  return (
    <>
      <button onClick={() => setOn(false)}>leave</button>
      <Routes><Route element={<AppShell />}><Route path="*" element={on ? <Page highlight={highlight} /> : <p>x</p>} /></Route></Routes>
    </>
  );
}
const renderAt = (highlight?: string) =>
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter initialEntries={["/m/a"]}><Harness highlight={highlight} /></MemoryRouter>
    </QueryClientProvider>,
  );

describe("useFocusLayout", () => {
  Element.prototype.scrollIntoView = vi.fn(); // jsdom has none
  it("hides the tool sub-nav while mounted and restores it after unmount", () => {
    renderAt();
    expect(screen.queryByRole("navigation", { name: /Công cụ/ })).toBeNull();
    fireEvent.click(screen.getByText("leave"));
    expect(screen.getAllByRole("navigation", { name: /Công cụ/ }).length).toBeGreaterThan(0);
  });

  it("lights the highlighted rail entry instead of the route's group", () => {
    renderAt("/me/work");
    const cur = screen.getAllByRole("link", { current: "page" });
    expect(cur.length).toBeGreaterThan(0);
    for (const l of cur) expect(l.getAttribute("title")).toBe("Việc của tôi");
    fireEvent.click(screen.getByText("leave"));
    expect(screen.getAllByRole("link", { current: "page" }).some((l) => l.getAttribute("title") === "Công cụ")).toBe(true);
  });
});

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
