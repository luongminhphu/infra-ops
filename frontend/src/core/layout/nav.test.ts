import { describe, expect, it } from "vitest";
import { activeFor, railSections } from "./nav";

const NAV = [
  { module: "core", label: "Hệ thống", items: [
    { label: "Trang chủ", path: "/", icon: "layout-dashboard", permission: "x" },
    { label: "Việc của tôi", path: "/me/work", icon: "user-check", permission: "x" },
    { label: "Tổng quan số liệu", path: "/overview", icon: "layout-dashboard", permission: "x", group: "Thống kê", group_order: 4, section: "reports" as const },
    { label: "Kết nối hệ thống", path: "/settings/connections", icon: "plug", permission: "x" },
  ] },
  { module: "lifecycle", label: "Vòng đời nhân sự", items: [
    { label: "Lưu đồ", path: "/lifecycle/flows", icon: "workflow", permission: "x", group: "Quy trình & cấu hình", group_order: 3, section: "tools" as const },
    { label: "Đối soát", path: "/lifecycle/reconciliation", icon: "git-compare-arrows", permission: "x", group: "Xử lý công việc", group_order: 0, section: "tools" as const },
    { label: "Chấm công", path: "/lifecycle/attendance", icon: "clock", permission: "x", group: "Báo cáo rà soát", group_order: 5, section: "reports" as const },
    { label: "Không nhóm", path: "/lifecycle/other", icon: "box", permission: "x" },
  ] },
  { module: "playbook", label: "Infra Playbook", items: [{ label: "Thư viện Playbook", path: "/playbook", icon: "book-open", permission: "x" }] },
  { module: "workload", label: "Công việc Infra Support", items: [
    { label: "Công việc", path: "/workload/tickets", icon: "list-checks", permission: "x", group: "Xử lý công việc", group_order: 0, section: "tools" as const },
    { label: "Thống kê", path: "/workload/stats", icon: "chart-column", permission: "x", group: "Thống kê", group_order: 4, section: "reports" as const },
  ] },
];

describe("rail navigation", () => {
  const sections = railSections(NAV);
  it("is Playbook, my work, tools, reports and system at the bottom", () => {
    expect(sections.map((s) => [s.id, s.label])).toEqual([["home", "Playbook"], ["mine", "Việc của tôi"], ["tools", "Công cụ"], ["reports", "Báo cáo"], ["system", "Hệ thống"]]);
    expect(sections[0]!.items.map((i) => i.path)).toEqual(["/", "/playbook"]);
    expect(sections.at(-1)!.bottom).toBe(true);
  });
  it("groups pages of every module by heading, in the backend's heading order", () => {
    const tools = sections[2]!.items;
    expect(tools.map((i) => i.path)).toEqual(["/tools", "/lifecycle/reconciliation", "/workload/tickets", "/lifecycle/flows", "/lifecycle/other"]);
    expect(tools.map((i) => i.group)).toEqual([undefined, "Xử lý công việc", "Xử lý công việc", "Quy trình & cấu hình", "Vòng đời nhân sự"]);
  });
  it("puts statistics and review reports under Báo cáo", () => {
    const reports = sections[3]!.items;
    expect(reports.map((i) => i.path)).toEqual(["/overview", "/workload/stats", "/lifecycle/attendance"]);
    expect(activeFor(sections, "/lifecycle/attendance").section!.id).toBe("reports");
    expect(sections.at(-1)!.items.map((i) => i.path)).toEqual(["/settings/connections"]); // not under Hệ thống
  });
  it("finds the active section and item", () => {
    expect(activeFor(sections, "/").section!.id).toBe("home");
    expect(activeFor(sections, "/playbook/abc").section!.id).toBe("home");
    expect(activeFor(sections, "/me/work").section!.id).toBe("mine");
    expect(activeFor(sections, "/lifecycle/flows").item!.label).toBe("Lưu đồ");
    expect(activeFor(sections, "/settings/connections").section!.id).toBe("system");
    expect(activeFor(sections, "/lifecycle/jobs/123")).toMatchObject({ item: null, section: { id: "tools" } });
    expect(activeFor(sections, "/unknown").section).toBeNull();
  });
});
