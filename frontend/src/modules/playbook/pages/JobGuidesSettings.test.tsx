import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const h = vi.hoisted(() => ({ perms: new Set<string>(), api: vi.fn() }));
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: (p: string) => h.perms.has(p), me: { permissions: ["x"] } }) }));
vi.mock("@/core/api/client", async (orig) => ({ ...(await orig<typeof import("@/core/api/client")>()), api: h.api }));

import { playbookModule } from "../index";
import { JobGuidesSettings } from "./JobGuidesSettings";

const wrap = (el: React.ReactNode) => render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><MemoryRouter>{el}</MemoryRouter></QueryClientProvider>,
);

describe("JobGuidesSettings", () => {
  beforeEach(() => {
    h.perms = new Set(["playbook.guides.publish"]); h.api.mockReset();
    h.api.mockImplementation(async (url: string, init?: { method?: string }) => {
      if (url.includes("/settings/job-guides")) return { workflow_guides: { a: "gone", b: "g1", old: "g1" }, workflows: [{ key: "a", title: "Quy trình A" }, { key: "b", title: "Quy trình B" }] };
      if (init?.method === "PUT") return { workflow_guides: {}, workflows: [] };
      return { items: [{ id: "g1", title: "Onboard", published_version: 2 }, { id: "g2", title: "Nháp", published_version: null }] };
    });
  });

  it("labels an orphan guide and never sends orphan ids or unknown workflows", async () => {
    wrap(<JobGuidesSettings />);
    expect(await screen.findByRole("option", { name: "(Playbook đã xoá)" })).toBeTruthy();
    expect(screen.queryByRole("option", { name: /Nháp/ })).toBeNull();
    fireEvent.change(screen.getAllByRole("combobox")[1]!, { target: { value: "" } });
    fireEvent.change(screen.getAllByRole("combobox")[1]!, { target: { value: "g1" } });
    fireEvent.click(screen.getByRole("button", { name: /Lưu/ }));
    await waitFor(() => expect(h.api).toHaveBeenCalledWith("/api/playbook/settings/job-guides", { method: "PUT", json: { workflow_guides: { b: "g1" } } }));
  });

  it("route is guarded by playbook.guides.publish", () => {
    h.perms = new Set(["playbook.guides.view"]);
    const route = playbookModule.routes.find((r) => r.path === "playbook/job-guides")!;
    wrap(route.element);
    expect(screen.getByText("Bạn chưa có quyền xem trang này")).toBeTruthy();
  });
});
