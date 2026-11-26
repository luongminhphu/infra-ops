import { describe, expect, it } from "vitest";
import { looksLikeTable, parseIds } from "./pages/AttendancePage";

describe("attendance input", () => {
  it("counts only ID-like tokens and sends tables to the server", () => {
    expect(parseIds("003551, 003475\n003551 Nguyễn Văn An")).toEqual(["003551", "003475"]);
    expect(looksLikeTable("1\t003551\tNguyễn Văn An")).toBe(true);
    expect(looksLikeTable("Nguyễn A - 003551")).toBe(true);
    expect(looksLikeTable("003551, 003475")).toBe(false);
  });
});
