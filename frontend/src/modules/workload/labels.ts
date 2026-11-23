import type { Status } from "@/core/ui/status";
import type { Ticket } from "./api";

const DAY = 86_400_000;
/** SDP statuses that the progress badge already says; only other ones (On Hold…) are worth a line. */
export const USUAL_SDP_STATUS = new Set(["open", "resolved", "closed", "canceled", "cancelled", "completed"]);

/**
 * Progress badge for a ticket. When most tasks are overdue a single red "Quá hạn" says nothing, so the
 * label carries how late it is and only a week or more turns red.
 */
export function ticketBadge(t: Pick<Ticket, "state" | "overdue" | "due_by">, now: Date = new Date()): { status: Status; label: string } {
  if (t.state === "done") return t.overdue ? { status: "warning", label: "Xong, trễ hạn" } : { status: "done", label: "Xong đúng hạn" };
  if (t.state === "cancelled") return { status: "skipped", label: "Đã huỷ" };
  if (!t.overdue || !t.due_by) return { status: "waiting", label: "Đang chờ" };
  const days = Math.floor((now.getTime() - new Date(t.due_by).getTime()) / DAY);
  if (days < 1) return { status: "warning", label: "Trễ dưới 1 ngày" };
  return { status: days >= 7 ? "failed" : "warning", label: `Trễ ${days} ngày` };
}
