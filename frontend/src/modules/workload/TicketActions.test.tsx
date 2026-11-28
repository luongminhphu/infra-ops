import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { beforeAll, beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const canMock = vi.hoisted(() => vi.fn());
vi.mock("@/core/api/client", () => ({ api: apiMock, errorMessage: String }));
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: canMock }) }));

import { TicketActions } from "./TicketActions";
import type { Ticket } from "./api";

const ticket = { id: "501", display_id: "501", subject: "Chuột hỏng", requester_name: "A", technician_id: "11", state: "open", device_need: null } as Ticket;

const renderMenu = (t: Ticket = ticket) =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter><TicketActions ticket={t} /></MemoryRouter>
    </QueryClientProvider>,
  );

describe("TicketActions", () => {
  beforeAll(() => {
    // jsdom lacks the native <dialog> methods used by core Dialog.
    HTMLDialogElement.prototype.showModal ??= function (this: HTMLDialogElement) { this.setAttribute("open", ""); };
    HTMLDialogElement.prototype.close ??= function (this: HTMLDialogElement) { this.removeAttribute("open"); };
  });

  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockResolvedValue({ items: [], me: "11", view_all: false, unassigned: { open: 0, overdue: 0 } });
  });

  it("is hidden without workload.tickets.update", async () => {
    canMock.mockImplementation((p: string) => p !== "workload.tickets.update");
    renderMenu();
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("button", { name: /Thao tác/ })).toBeNull();
  });

  it("is hidden for another operator's ticket and for closed tickets", async () => {
    canMock.mockImplementation((p: string) => p === "workload.tickets.update");
    renderMenu({ ...ticket, technician_id: "12" });
    renderMenu({ ...ticket, state: "done" });
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByRole("button", { name: /Thao tác/ })).toBeNull();
  });

  it("offers note and close for the operator's own ticket, purchase only with its permission", async () => {
    canMock.mockImplementation((p: string) => p === "workload.tickets.update");
    renderMenu();
    await userEvent.click(await screen.findByRole("button", { name: /Thao tác/ }));
    expect(screen.getByRole("menuitem", { name: /Thêm ghi chú/ })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /Đóng request/ })).toBeTruthy();
    expect(screen.queryByRole("menuitem", { name: /đề xuất mua/ })).toBeNull();
  });

  const openClose = async () => {
    await userEvent.click(await screen.findByRole("button", { name: /Thao tác/ }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Đóng request/ }));
    await userEvent.type(await screen.findByRole("textbox", { name: /Kết quả xử lý/ }), "Đã thay chuột.");
    apiMock.mockResolvedValueOnce({ id: "job-1", params: { close_status: "Resolved" } });
    await userEvent.click(screen.getByRole("button", { name: "Tạo yêu cầu đóng" }));
  };

  it("posts the typed request number to confirm the close step", async () => {
    canMock.mockImplementation((p: string) => p === "workload.tickets.update");
    renderMenu();
    await openClose();
    expect((await screen.findByRole("status")).textContent).toContain("“Resolved”");
    expect(screen.queryByRole("link", { name: /Mở Job/ })).toBeNull();
    await userEvent.type(screen.getByRole("textbox", { name: /Số request/ }), "501");
    apiMock.mockResolvedValueOnce({});
    await userEvent.click(screen.getByRole("button", { name: "Xác nhận đóng" }));
    expect(apiMock).toHaveBeenCalledWith("/api/core/jobs/job-1/steps/status/confirm", { method: "POST", json: { typed: "501" } });
  });

  it("shows the open-job message, with a link only for people who may view Jobs", async () => {
    canMock.mockImplementation((p: string) => p === "workload.tickets.update" || p === "lifecycle.job.view");
    renderMenu();
    await userEvent.click(await screen.findByRole("button", { name: /Thao tác/ }));
    await userEvent.click(screen.getByRole("menuitem", { name: /Đóng request/ }));
    await userEvent.type(await screen.findByRole("textbox", { name: /Kết quả xử lý/ }), "Đã thay chuột.");
    apiMock.mockRejectedValueOnce(Object.assign(new Error("Request đang có Job chưa xong"), { details: { job_id: "j9" } }));
    await userEvent.click(screen.getByRole("button", { name: "Tạo yêu cầu đóng" }));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByRole("link", { name: /Mở Job đang chạy/ })).toBeTruthy();
  });
});
