import { describe, expect, it } from "vitest";
import { addDevice, addLevel, moveDevice, moveLevel, overlappingKeys, PRESET, removeDevice, removeLevel, tiersFor, type Matrix } from "./pages/AllocationMatrix";

const tiers = ["PC - Standard", "PC - Performance", "Laptop - Standard"].map((name) => ({ name, cpu: "", ram: "", storage: "", description: "", catalog_name: "" }));
const dev = (label: string, t = label) => ({ label, product_types: [t], product_contains: "", catalog_name: "", note: "" });
const lvl = (name: string, titles: string[], departments: string[] = []) => ({ name, titles, bands: [], departments, group: "" });

describe("allocation matrix", () => {
  it("offers a column only the tiers named after it", () => {
    expect(tiersFor(dev("PC", "Desktop"), tiers).map((t) => t.name)).toEqual(["PC - Standard", "PC - Performance"]);
    expect(tiersFor(dev("Máy tính xách tay", "Laptop"), tiers).map((t) => t.name)).toEqual(["Laptop - Standard"]);
    expect(tiersFor(dev("Màn hình", "Monitor"), tiers)).toHaveLength(0);
  });

  it("keeps cells in step when rows and columns are added or removed", () => {
    let m: Matrix = { devices: [], levels: [], cells: [] };
    m = addDevice(addLevel(addLevel(m, lvl("A", ["1"])), lvl("B", ["2"])), dev("PC"));
    m = addDevice(m, dev("Laptop"));
    expect(m.cells.map((r) => r.length)).toEqual([2, 2]);
    m = removeLevel(removeDevice(m, 0), 0);
    expect(m.levels.map((l) => l.name)).toEqual(["B"]);
    expect(m.cells).toEqual([[{ on: false, quantity: 1, tier: "", reuse_only: false }]]);
  });

  it("flags a title keyword two general rows list, not a department row", () => {
    expect(overlappingKeys([lvl("NV", ["Chuyên viên", "Manager"]), lvl("IT", ["Manager"], ["IT"]), lvl("QL", ["manager", "Director"])])).toEqual(["Manager"]);
  });

  it("moves a column or row with its cells", () => {
    let m: Matrix = { devices: [], levels: [], cells: [] };
    m = addLevel(addLevel(m, lvl("A", [])), lvl("B", []));
    for (const name of ["PC", "Điện thoại bàn", "Phụ kiện"]) m = addDevice(m, dev(name));
    m = { ...m, cells: m.cells.map((r, i) => r.map((c, j) => ({ ...c, quantity: i * 10 + j + 1 }))) };
    m = moveDevice(addDevice(m, dev("UPS")), 3, 2); // new UPS column right after "Điện thoại bàn"
    expect(m.devices.map((d) => d.label)).toEqual(["PC", "Điện thoại bàn", "UPS", "Phụ kiện"]);
    expect(m.cells[0]!.map((c) => c.quantity)).toEqual([1, 2, 1, 3]);
    m = moveLevel(m, 1, 0);
    expect(m.levels.map((l) => l.name)).toEqual(["B", "A"]);
    expect(m.cells[0]![0]!.quantity).toBe(11);
  });

  it("preset puts UPS right after the desk phone", () => {
    const labels = PRESET.devices.map((d) => d.label);
    expect(labels.indexOf("UPS")).toBe(labels.indexOf("Điện thoại bàn") + 1);
    expect(PRESET.cells.every((r) => r.length === PRESET.devices.length)).toBe(true);
  });
});
