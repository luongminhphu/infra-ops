import { describe, expect, it } from "vitest";
import { viewItems, type Item } from "./pages/StocktakePage";

const item = (id: number, over: Partial<Item>): Item => ({
  id, asset_id: String(id), name: `LT-${id}`, product_type: "Laptop", asset_tag: `TAG${id}`, serial: `SN${id}`, fa_code: null,
  state: "In Use", user_name: "Nguyễn An", site: "HCM", count: "pending", condition: null, seen_at: "", note: "",
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
  it("searches names, codes, user and site", () => {
    expect(viewItems(items, "all", "sn2").map((i) => i.id)).toEqual([2]);
    expect(viewItems(items, "all", "  tag4 ").map((i) => i.id)).toEqual([4]);
    expect(viewItems(items, "all", "nguyễn").length).toBe(4);
  });
});
