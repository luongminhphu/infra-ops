import { describe, expect, it } from "vitest";
import { ticketBadge } from "./labels";

const now = new Date("2026-11-23T10:00:00Z");
const open = (due: string) => ({ state: "open" as const, overdue: true, due_by: due });

describe("ticketBadge", () => {
  it("says how late an open ticket is and turns red only after a week", () => {
    expect(ticketBadge(open("2026-11-23T02:00:00Z"), now)).toEqual({ status: "warning", label: "Trễ dưới 1 ngày" });
    expect(ticketBadge(open("2026-11-20T09:00:00Z"), now)).toEqual({ status: "warning", label: "Trễ 3 ngày" });
    expect(ticketBadge(open("2026-11-10T10:00:00Z"), now)).toEqual({ status: "failed", label: "Trễ 13 ngày" });
  });
  it("keeps the other states", () => {
    expect(ticketBadge({ state: "open", overdue: false, due_by: null }, now).label).toBe("Đang chờ");
    expect(ticketBadge({ state: "done", overdue: true, due_by: "2026-11-01T00:00:00Z" }, now).label).toBe("Xong, trễ hạn");
    expect(ticketBadge({ state: "cancelled", overdue: false, due_by: null }, now).label).toBe("Đã huỷ");
  });
});
