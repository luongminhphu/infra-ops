import { describe, expect, it } from "vitest";
import { activeFor, railSections } from "./nav";

const NAV = [
  { module: "core", label: "Hệ thống", items: [
    { label: "Tổng quan", path: "/", icon: "layout-dashboard", permission: "x" },
    { label: "Việc của tôi", path: "/me/work", icon: "user-check", permission: "x" },
    { label: "Kết nối hệ thống", path: "/settings/connections", icon: "plug", permission: "x" },
  ] },
  { module: "lifecycle", label: "Vòng đời nhân sự", items: [
    { label: "Đối soát", path: "/lifecycle/reconciliation", icon: "git-compare-arrows", permission: "x", group: "Việc cần làm" },
    { label: "Lưu đồ", path: "/lifecycle/flows", icon: "workflow", permission: "x" },
  ] },
  { module: "playbook", label: "Infra Playbook", items: [{ label: "Thư viện Playbook", path: "/playbook", icon: "book-open", permission: "x" }] },
  { module: "workload", label: "Công việc Infra Support", items: [] },
];

describe("rail navigation", () => {
  const sections = railSections(NAV);
  it("is Playbook, my work, tools (every module page) and system at the bottom", () => {
    expect(sections.map((s) => [s.id, s.label])).toEqual([["home", "Playbook"], ["mine", "Việc của tôi"], ["tools", "Công cụ"], ["system", "Hệ thống"]]);
    expect(sections[0]!.items.map((i) => i.path)).toEqual(["/", "/playbook"]);
    const tools = sections[2]!.items;
    expect(tools.map((i) => i.path)).toEqual(["/tools", "/overview", "/lifecycle/reconciliation", "/lifecycle/flows"]);
    expect(tools[2]!.group).toBe("Vòng đời nhân sự"); // module pages grouped under the module name
    expect(sections.at(-1)!.bottom).toBe(true);
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
