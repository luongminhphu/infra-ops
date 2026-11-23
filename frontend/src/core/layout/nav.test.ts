import { describe, expect, it } from "vitest";
import { activeFor, railSections } from "./nav";

const NAV = [
  { module: "core", label: "Hệ thống", items: [
    { label: "Tổng quan", path: "/", icon: "layout-dashboard", permission: "x" },
    { label: "Kết nối hệ thống", path: "/settings/connections", icon: "plug", permission: "x" },
  ] },
  { module: "lifecycle", label: "Vòng đời nhân sự", items: [
    { label: "Đối soát", path: "/lifecycle/reconciliation", icon: "git-compare-arrows", permission: "x" },
    { label: "Lưu đồ", path: "/lifecycle/flows", icon: "workflow", permission: "x" },
  ] },
  { module: "workload", label: "Công việc Helpdesk", items: [] },
];

describe("rail navigation", () => {
  const sections = railSections(NAV);
  it("orders home, modules, then system at the bottom and drops empty modules", () => {
    expect(sections.map((s) => [s.id, s.label])).toEqual([["home", "Tổng quan"], ["lifecycle", "Vòng đời"], ["system", "Hệ thống"]]);
    expect(sections.at(-1)!.bottom).toBe(true);
  });
  it("finds the active section and item", () => {
    expect(activeFor(sections, "/").section!.id).toBe("home");
    expect(activeFor(sections, "/lifecycle/flows").item!.label).toBe("Lưu đồ");
    expect(activeFor(sections, "/settings/connections").section!.id).toBe("system");
    // Detail pages without a nav entry still highlight their module.
    expect(activeFor(sections, "/lifecycle/jobs/123")).toMatchObject({ item: null, section: { id: "lifecycle" } });
    expect(activeFor(sections, "/unknown").section).toBeNull();
  });
});
