import { describe, expect, it } from "vitest";
import { addDevice, addLevel, overlappingBands, removeDevice, removeLevel, tiersFor, type Matrix } from "./pages/AllocationMatrix";

const tiers = ["PC - Standard", "PC - Performance", "Laptop - Standard"].map((name) => ({ name, cpu: "", ram: "", storage: "", description: "", catalog_name: "" }));
const dev = (label: string, t = label) => ({ label, product_types: [t], product_contains: "", catalog_name: "", note: "" });
const lvl = (name: string, bands: string[], departments: string[] = []) => ({ name, bands, departments, group: "" });

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
    expect(m.cells).toEqual([[{ on: false, quantity: 1, tier: "" }]]);
  });

  it("flags a band two general rows list, not a department row", () => {
    expect(overlappingBands([lvl("1–6", ["4", "5"]), lvl("IT", ["4"], ["IT"]), lvl("5–9", ["5", "6"])])).toEqual(["5"]);
  });
});
