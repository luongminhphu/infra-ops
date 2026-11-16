import { formatDate, formatDateTime, pluralDays, formatRelative } from "./format";

describe("format (Asia/Ho_Chi_Minh)", () => {
  it("formats dd/MM/yyyy in Vietnam time even across UTC midnight", () => {
    expect(formatDate("2026-11-16T18:30:00Z")).toBe("17/11/2026");
    expect(formatDateTime("2026-11-16T18:30:00Z")).toBe("17/11/2026 01:30");
  });
  it("falls back for empty or invalid input", () => {
    expect(formatDate(null)).toBe("—");
    expect(formatDate("not a date")).toBe("—");
  });
  it("describes remaining days", () => {
    expect(pluralDays(3)).toBe("còn 3 ngày");
    expect(pluralDays(0)).toBe("hết hạn hôm nay");
    expect(pluralDays(-2)).toBe("quá hạn 2 ngày");
  });
  it("uses relative wording for recent times", () => {
    const now = new Date("2026-11-16T10:00:00Z");
    expect(formatRelative("2026-11-16T09:55:00Z", now)).toBe("5 phút trước");
    expect(formatRelative(null, now)).toBe("Chưa có");
  });
});
