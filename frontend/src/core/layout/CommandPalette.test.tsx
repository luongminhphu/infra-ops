import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";

vi.mock("@/core/auth/AuthProvider", () => ({
  useAuth: () => ({
    me: { nav: [{ module: "core", label: "Core", items: [{ label: "Trang chủ", path: "/", icon: "layout-dashboard", permission: "x" }] }] },
  }),
}));

const { CommandPalette } = await import("./CommandPalette");

const body = {
  groups: [{ key: "core.jobs", label: "Job", items: [{ title: "Nguyễn Văn An", detail: "Onboard · draft", href: "/lifecycle/jobs/1", kind: "job" }] }],
  failed: ["Workload"],
};

HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) {
  this.setAttribute("open", "");
};

afterEach(() => vi.unstubAllGlobals());

function setup() {
  const fetchMock = vi.fn(async () => new Response(JSON.stringify(body), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><CommandPalette open onClose={() => {}} /></MemoryRouter>
    </QueryClientProvider>,
  );
  return fetchMock;
}

describe("CommandPalette search", () => {
  it("requests nothing under 2 characters, then shows grouped records and failed sources", async () => {
    const fetchMock = setup();
    const input = screen.getByRole("combobox");
    await userEvent.type(input, "n");
    await new Promise((r) => setTimeout(r, 350));
    expect(fetchMock).not.toHaveBeenCalled();

    await userEvent.type(input, "g");
    expect(await screen.findByText("Nguyễn Văn An")).toBeTruthy();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String((fetchMock.mock.calls as unknown[][])[0]![0])).toBe("/api/core/search?q=ng");
    expect(screen.getByText("Job")).toBeTruthy();
    await waitFor(() => expect(screen.getByText(/Chưa tìm được trong: Workload/)).toBeTruthy());
  });
});
