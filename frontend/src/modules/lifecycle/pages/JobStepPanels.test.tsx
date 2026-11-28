import { fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const perms = vi.hoisted(() => ({ assign: true }));
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: (p: string) => perms.assign && p === "lifecycle.job.assign" }) }));

import { AccountPlanForm, DraftConfig, MailboxHandoverPanel, RtrChecklistPanel, TransferPlanForm } from "./JobStepPanels";
import type { Job, JobStep } from "../jobs";

const step = (output: Record<string, unknown>) => ({ key: "x", output } as unknown as JobStep);
const plan = {
  level: "L2", employee_id: "003551", cn: "Nguyen Van An", ou: "OU=Users,DC=c,DC=l", ou_rule: "Kinh Doanh",
  software: ["Office"], license_names: ["E3"], warnings: ["Cảnh báo thử"],
  candidates: [{ sam: "an.nv", upn: "an.nv@c.vn", free: false }, { sam: "an.nv1", upn: "an.nv1@c.vn", free: true }],
  proposed_sam: "an.nv1",
};
const ui = (onSubmit = vi.fn()) => {
  render(<QueryClientProvider client={new QueryClient()}><AccountPlanForm step={step(plan)} busy={false} onSubmit={onSubmit} /></QueryClientProvider>);
  return onSubmit;
};

describe("AccountPlanForm", () => {
  beforeEach(() => { perms.assign = true; });
  it("preselects the proposed username, disables taken ones and submits {sam}", () => {
    const onSubmit = ui();
    expect((screen.getByRole("radio", { name: /an\.nv1/ }) as HTMLInputElement).checked).toBe(true);
    expect((screen.getByRole("radio", { name: /an\.nv\b.*đã dùng/ }) as HTMLInputElement).disabled).toBe(true);
    expect(screen.getByText("Cảnh báo thử")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Chốt tài khoản này" }));
    expect(onSubmit).toHaveBeenCalledWith({ sam: "an.nv1" });
  });
  it("explains the dead end when there is no proposal and no assign permission", () => {
    perms.assign = false;
    render(<QueryClientProvider client={new QueryClient()}><AccountPlanForm step={step({ ...plan, proposed_sam: null, warnings: [], candidates: [{ sam: "an.nv", upn: "an.nv@c.vn", free: false }] })} busy={false} onSubmit={vi.fn()} /></QueryClientProvider>);
    expect(screen.getByText(/chưa đề xuất được username/)).toBeTruthy();
  });
  it("hides custom username / OU without lifecycle.job.assign", () => {
    perms.assign = false;
    ui();
    expect(screen.queryByText("Nhập username khác")).toBeNull();
    expect(screen.queryByLabelText("OU tạo tài khoản")).toBeNull();
  });
});

describe("RtrChecklistPanel", () => {
  it("shows the summary and copies it", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(<RtrChecklistPanel step={step({ summary: "Onboard – An (Mã NV 003551)" })} />);
    expect(screen.getByLabelText("Tóm tắt RTR").textContent).toContain("003551");
    fireEvent.click(screen.getByRole("button", { name: "Sao chép" }));
    expect(writeText).toHaveBeenCalledWith("Onboard – An (Mã NV 003551)");
    expect(await screen.findByText("Đã sao chép")).toBeTruthy();
  });
});

const offboard = (params: Record<string, unknown>) => ({ id: "j1", params: { kind: "offboard", ...params } } as unknown as Job);
const draft = (job: Job) => render(<QueryClientProvider client={new QueryClient()}><DraftConfig job={job} saving={false} onSave={vi.fn()} /></QueryClientProvider>);

describe("mailbox option", () => {
  it("is hidden when the job has no Microsoft steps", () => {
    draft(offboard({ microsoft: false }));
    expect(screen.queryByRole("switch")).toBeNull();
    expect(screen.getByText(/không có bước Microsoft/)).toBeTruthy();
  });
  it("shows the switch when the job has Microsoft steps", () => {
    draft(offboard({ microsoft: true }));
    expect(screen.getByRole("switch")).toBeTruthy();
  });
  it("offers no complete button for mailbox_handover when the option is off, only when it is on", () => {
    const { unmount } = render(<MailboxHandoverPanel job={offboard({ microsoft: true, options: { mailbox_handover: false } })} step={{ message: "m" } as JobStep} busy={false} onComplete={vi.fn()} />);
    expect(screen.queryByRole("button", { name: "Đánh dấu hoàn tất" })).toBeNull();
    unmount();
    render(<MailboxHandoverPanel job={offboard({ microsoft: true, options: { mailbox_handover: true } })} step={{ message: "m" } as JobStep} busy={false} onComplete={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Đánh dấu hoàn tất" })).toBeTruthy();
  });
});

describe("AccountPlanForm re-prepare", () => {
  it("shows Chuẩn bị lại when the plan has warnings", () => {
    const again = vi.fn();
    render(<QueryClientProvider client={new QueryClient()}><AccountPlanForm step={step(plan)} busy={false} onSubmit={vi.fn()} onReprepare={again} /></QueryClientProvider>);
    fireEvent.click(screen.getByRole("button", { name: "Chuẩn bị lại" }));
    expect(again).toHaveBeenCalledTimes(1);
  });
});

describe("TransferPlanForm", () => {
  beforeEach(() => { perms.assign = true; });
  const tplan = {
    ou: "OU=Old,DC=c,DC=l", target_ou: "OU=New,DC=c,DC=l", ou_options: ["OU=New,DC=c,DC=l", "OU=Other,DC=c,DC=l"],
    current: { title: "Nhân viên", department: "Kho", manager: "CN=Old" }, attributes: { title: "Trưởng nhóm", department: "Kinh Doanh" },
    add: ["CN=Sales,DC=c"], remove: ["CN=Kho,DC=c", "CN=Cu,DC=c"], effective_date: "2026-12-01", sources: { org: "request_old" }, warnings: ["Cảnh báo chuyển"],
  };
  const show = (output: Record<string, unknown> = tplan, onSubmit = vi.fn()) => {
    render(<TransferPlanForm step={step(output)} busy={false} onSubmit={onSubmit} />);
    return onSubmit;
  };
  it("shows before and after with the source of each value", () => {
    show();
    expect(screen.getByText("Kho")).toBeTruthy();
    expect(screen.getByText("Trưởng nhóm")).toBeTruthy();
    expect(screen.getAllByText("Request cũ")).toHaveLength(2);
    expect(screen.getAllByText("Request mới").length).toBeGreaterThan(0);
    expect(screen.getByText("Cảnh báo chuyển")).toBeTruthy();
    expect(screen.getByText("Áp dụng ngày 01/12/2026")).toBeTruthy();
    expect(screen.getByText("CN=Sales,DC=c")).toBeTruthy();
  });
  it("submits only the removals that stay checked", () => {
    const onSubmit = show();
    fireEvent.click(screen.getByRole("checkbox", { name: "CN=Cu,DC=c" }));
    fireEvent.click(screen.getByRole("button", { name: "Chốt kế hoạch thuyên chuyển" }));
    expect(onSubmit).toHaveBeenCalledWith({ remove: ["CN=Kho,DC=c"] });
  });
  it("sends a changed OU", () => {
    const onSubmit = show();
    fireEvent.change(screen.getByLabelText("OU đích"), { target: { value: "OU=Other,DC=c,DC=l" } });
    fireEvent.click(screen.getByRole("button", { name: "Chốt kế hoạch thuyên chuyển" }));
    expect(onSubmit).toHaveBeenCalledWith({ remove: ["CN=Kho,DC=c", "CN=Cu,DC=c"], ou: "OU=Other,DC=c,DC=l" });
  });
  it("locks removals and OU without lifecycle.job.assign", () => {
    perms.assign = false;
    show();
    expect((screen.getByRole("checkbox", { name: "CN=Cu,DC=c" }) as HTMLInputElement).disabled).toBe(true);
    expect(screen.queryByLabelText("OU đích")).toBeNull();
  });
  it("asks for the effective date when the request has none and sends it", () => {
    const onSubmit = show({ ...tplan, effective_date: null });
    const btn = screen.getByRole("button", { name: "Chốt kế hoạch thuyên chuyển" }) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Ngày hiệu lực"), { target: { value: "2026-12-05" } });
    expect(btn.disabled).toBe(false);
    fireEvent.click(btn);
    expect(onSubmit).toHaveBeenCalledWith({ remove: ["CN=Kho,DC=c", "CN=Cu,DC=c"], effective_date: "2026-12-05" });
  });
  it("lists license and BioStar lines; unchecked removals are sent back", () => {
    const onSubmit = show({
      ...tplan, remove: [],
      licenses: { add: ["sku-e3"], remove: ["sku-f1", "sku-x"], names: { "sku-e3": "E3", "sku-f1": "F1", "sku-x": "Visio" }, level: "Manager", level_why: "hàng mặc định" },
      biostar: { add: ["20"], remove: ["10"] },
    });
    expect(screen.getByText("License thêm (cấp Manager – hàng mặc định) (1)")).toBeTruthy();
    expect(screen.getByText("E3")).toBeTruthy();
    expect(screen.getByText("Nhóm 20")).toBeTruthy();
    fireEvent.click(screen.getByRole("checkbox", { name: "Visio" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Nhóm 10" }));
    fireEvent.click(screen.getByRole("button", { name: "Chốt kế hoạch thuyên chuyển" }));
    expect(onSubmit).toHaveBeenCalledWith({ remove: [], remove_licenses: ["sku-f1"], remove_biostar: [] });
  });
  it("offers Chuẩn bị lại for a blocked plan only", () => {
    const again = vi.fn();
    const { unmount } = render(<TransferPlanForm step={step({ blocked: true, warnings: ["Không đọc được AD"] })} busy={false} onSubmit={vi.fn()} onReprepare={again} />);
    fireEvent.click(screen.getByRole("button", { name: "Chuẩn bị lại" }));
    expect(again).toHaveBeenCalledTimes(1);
    unmount();
    render(<TransferPlanForm step={step(tplan)} busy={false} onSubmit={vi.fn()} onReprepare={again} />);
    expect(screen.queryByRole("button", { name: "Chuẩn bị lại" })).toBeNull();
  });
  it("cannot be submitted while the plan is blocked", () => {
    show({ blocked: true, warnings: ["Không đọc được AD"] });
    expect((screen.getByRole("button", { name: "Chốt kế hoạch thuyên chuyển" }) as HTMLButtonElement).disabled).toBe(true);
  });
});
