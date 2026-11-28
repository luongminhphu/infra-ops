import { describe, expect, it } from "vitest";
import { fmtVn, idsKey, looksLikeTable, mergeIds, monthRange, nameKey, parseIds, periodErrors, previewSummary } from "./pages/AttendancePage";

describe("attendance input", () => {
  it("counts IDs by the server's rule for plain lists (4–8 digits, leading zeros restored)", () => {
    expect(parseIds("003551, 003475\n(3551); 12 123456789 AB1234")).toEqual(["003551", "003475"]);
    expect(parseIds("1 3551\n2 3475")).toEqual(["003551", "003475"]); // row numbers are not IDs
  });

  it("sends tables to the server", () => {
    expect(looksLikeTable("1\t003551\tNguyễn Văn An")).toBe(true);
    expect(looksLikeTable("STT Mã NV\n1 3551")).toBe(true);
    expect(looksLikeTable("003551, 003475")).toBe(false);
  });

  it("keys a set of IDs by content, so a new array each render does not retrigger the debounce", () => {
    expect(idsKey(["003475", "003551"])).toBe(idsKey(["003551", "003475"]));
    expect(idsKey([])).toBe("");
  });

  it("adds pasted IDs to the list instead of replacing it", () => {
    expect(mergeIds(["003999"], ["003551", "003999"])).toEqual(["003999", "003551"]);
  });
});

describe("attendance period", () => {
  const today = "2026-11-26";
  it("puts each error under its own field", () => {
    expect(periodErrors("2026-11-01", "2026-11-26", false, false, today)).toMatchObject({ fromError: "", toError: "", days: 26 });
    expect(periodErrors("2026-11-01", "2026-11-01", false, false, today).toError).toBe(""); // one day is fine
    expect(periodErrors("2026-11-10", "2026-11-01", false, false, today).toError).toBe("Không được trước ngày bắt đầu.");
    expect(periodErrors("2026-11-27", "2026-11-28", false, false, today).fromError).toBe("Ngày bắt đầu không được ở tương lai.");
    expect(periodErrors("2026-09-01", "2026-11-26", false, false, today).toError).toMatch(/^Tối đa 62 ngày/);
  });

  it("never exports a period the person did not see: a mistyped date is an error", () => {
    expect(periodErrors("2026-11-01", "2026-11-26", false, true, today).toError).toBe("Nhập ngày dạng dd/mm/yyyy.");
  });

  it("offers this month and last month", () => {
    const now = new Date(2026, 0, 15);
    expect(monthRange("this", now)).toEqual(["2026-01-01", "2026-01-15"]);
    expect(monthRange("last", now)).toEqual(["2025-12-01", "2025-12-31"]);
    expect(fmtVn("2025-12-31")).toBe("31/12/2025");
  });
});

describe("attendance preview", () => {
  it("compares names without accents, case or word order", () => {
    expect(nameKey("Nguyễn Văn An")).toBe(nameKey("AN. NGUYEN VAN"));
    expect(nameKey("Nguyễn Văn An")).not.toBe(nameKey("Trần Thị Bình"));
  });

  it("summarises the statuses", () => {
    const st = ["ok", "ok", "name_differs", "not_found", "unknown"] as const;
    expect(previewSummary(st.map((status) => ({ status })))).toBe("2 đúng · 1 lệch tên · 1 chưa thấy · 1 chưa rõ");
  });
});
