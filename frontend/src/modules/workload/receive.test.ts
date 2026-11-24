import { describe, expect, it } from "vitest";
import { assignSerials, suggestTag, typeCode } from "./receive";
import { bestMatch } from "./text";

describe("asset tag suggestion", () => {
  const codes = { Laptop: "LT", "Màn hình": "MN" };
  it("uses the configured code or the group initials", () => {
    expect(typeCode("laptop", codes)).toBe("LT");
    expect(typeCode("Man hinh", codes)).toBe("MN");
    expect(typeCode("Máy in màu", codes)).toBe("MIM");
    expect(typeCode("", codes)).toBe("TB");
  });
  it("builds HCM-year-type-last4", () => {
    expect(suggestTag("HCM", "2026", "LT", "5cd12-34xyz")).toBe("HCM-2026-LT-4XYZ");
    expect(suggestTag("HCM", "2026", "LT", "  ")).toBe("");
  });
});

describe("assignSerials", () => {
  const rows = [
    { serial: "MON1", asset_tag: "", text: "Màn hình Dell E2423H" },
    { serial: "LAP1", asset_tag: "", text: "Laptop Dell Latitude 5440" },
    { serial: "LAP2", asset_tag: "", text: "Laptop Dell Latitude 5440" },
  ];
  it("matches rows to devices by name, skipping filled slots", () => {
    const got = assignSerials(
      [
        { key: "l0", name: "Laptop Dell Latitude 5440", filled: false },
        { key: "l1", name: "Laptop Dell Latitude 5440", filled: true },
        { key: "m0", name: "Màn hình Dell 24", filled: false },
      ],
      rows,
    );
    expect(got.get("l0")?.serial).toBe("LAP1");
    expect(got.has("l1")).toBe(false);
    expect(got.get("m0")?.serial).toBe("MON1");
  });
  it("falls back to file order and stops when rows run out", () => {
    const got = assignSerials([1, 2, 3, 4].map((k) => ({ key: `s${k}`, name: "Chuột", filled: false })), rows);
    expect([...got.values()].map((r) => r.serial)).toEqual(["MON1", "LAP1", "LAP2"]);
  });
});

describe("bestMatch", () => {
  const cat = [{ name: "RAM 16GB DDR5 laptop" }, { name: "Laptop Dell Latitude 5440" }, { name: "Chuột Logitech M90" }];
  it("picks the clear winner only", () => {
    expect(bestMatch(cat, "Xin nâng cấp RAM 16GB cho laptop kế toán", (c) => c.name)?.name).toBe("RAM 16GB DDR5 laptop");
    expect(bestMatch(cat, "Reset mật khẩu email", (c) => c.name)).toBeUndefined();
    expect(bestMatch([{ name: "Laptop A" }, { name: "Laptop A" }], "laptop a mới", (c) => c.name)).toBeUndefined();
  });
});
