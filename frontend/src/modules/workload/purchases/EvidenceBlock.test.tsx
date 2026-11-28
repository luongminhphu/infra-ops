import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn().mockResolvedValue({}));
vi.mock("@/core/api/client", () => ({ api: apiMock, errorMessage: String }));
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ me: { upn: "a@x.vn" }, can: () => true }) }));

import { EvidenceBlock } from "./ProposalsTab";

beforeAll(() => {
  // jsdom lacks the native <dialog> methods used by core Dialog.
  HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
  HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) { this.removeAttribute("open"); };
});

describe("EvidenceBlock", () => {
  it("asks for confirmation before deleting an evidence file", async () => {
    const p = { id: "p1", status: "proposed", created_by: "a@x.vn", evidence: [{ id: "e1", filename: "duyet.pdf", created_by: "a@x.vn" }] };
    render(
      <QueryClientProvider client={new QueryClient()}>
        <MemoryRouter><EvidenceBlock p={p as never} onChange={() => {}} /></MemoryRouter>
      </QueryClientProvider>,
    );
    fireEvent.click(screen.getByRole("button", { name: "Xoá duyet.pdf" }));
    expect(apiMock).not.toHaveBeenCalled();
    fireEvent.click(await screen.findByRole("button", { name: "Xoá" }));
    await vi.waitFor(() => expect(apiMock).toHaveBeenCalledWith("/api/workload/purchases/p1/evidence/e1", { method: "DELETE" }));
  });
});
