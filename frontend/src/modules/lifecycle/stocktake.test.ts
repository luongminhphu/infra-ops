import { describe, expect, it } from "vitest";
import { person, viewItems, type Item } from "./pages/StocktakePage";
import { takenDepartments, type Area } from "./pages/StocktakeAreas";

const item = (id: number, over: Partial<Item>): Item => ({
  id, asset_id: String(id), name: `LT-${id}`, product_type: "Laptop", asset_tag: `TAG${id}`, serial: `SN${id}`, fa_code: null,
  state: "In Use", user_name: "Nguyễn An", employee_id: "003551", department: "Kế Toán", site: "HCM", zone_id: "z5", count: "pending", condition: null, seen_at: "", note: "",
  counted_by: null, resolution: null, resolution_note: "", difference: null, ...over,
});

describe("stocktake views", () => {
  const items = [
    item(1, {}),
    item(2, { count: "found", condition: "good" }),
    item(3, { count: "missing", difference: "Không tìm thấy khi kiểm" }),
    item(4, { count: "extra", asset_id: null, difference: "Có trên thực tế, không có trên SDP" }),
  ];
  it("filters by tab", () => {
    expect(viewItems(items, "pending", "").map((i) => i.id)).toEqual([1]);
    expect(viewItems(items, "missing", "").map((i) => i.id)).toEqual([3]);
    expect(viewItems(items, "differences", "").map((i) => i.id)).toEqual([3, 4]);
    expect(viewItems(items, "all", "").length).toBe(4);
  });
  it("filters by zone: mine, none, one, all", () => {
    const zoned = [item(1, {}), item(2, { zone_id: "z6" }), item(3, { zone_id: null })];
    expect(viewItems(zoned, "all", "", "mine", ["z6"]).map((i) => i.id)).toEqual([2]);
    expect(viewItems(zoned, "all", "", "none").map((i) => i.id)).toEqual([3]);
    expect(viewItems(zoned, "all", "", "z5").map((i) => i.id)).toEqual([1]);
    expect(viewItems(zoned, "all", "").length).toBe(3);
  });

  it("lists assets person by person", () => {
    const people = [item(1, { user_name: "Trần B" }), item(2, { user_name: "Lê A" }), item(3, { user_name: "Trần B" }), item(4, { user_name: null, employee_id: null })];
    expect(viewItems(people, "all", "").map((i) => i.id)).toEqual([4, 2, 1, 3]);
    expect(person(people[1]!)).toBe("Lê A · 003551");
    expect(person(people[3]!)).toBe("Không có người dùng");
  });

  it("searches names, codes, user and site", () => {
    expect(viewItems(items, "all", "sn2").map((i) => i.id)).toEqual([2]);
    expect(viewItems(items, "all", "  tag4 ").map((i) => i.id)).toEqual([4]);
    expect(viewItems(items, "all", "nguyễn").length).toBe(4);
  });
});

describe("stocktake areas", () => {
  it("knows which departments another area already has", () => {
    const areas = [{ id: "a", name: "Tầng 5", departments: ["Kế Toán"] }, { id: "b", name: "Tầng 6", departments: ["IT"] }] as Area[];
    expect([...takenDepartments(areas, "a")]).toEqual([["it", "Tầng 6"]]);
  });
});
