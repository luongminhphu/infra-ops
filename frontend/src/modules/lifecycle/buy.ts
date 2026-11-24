import { formatDate } from "@/core/lib/format";

/** Cross-module handoff: a pre-filled purchase proposal for a device the stock cannot cover. */
interface BuyLine {
  label: string;
  catalog_name: string;
  buy: number;
}
interface BuyRequest {
  id: string;
  display_id: string | null;
  full_name: string | null;
  employee_id: string | null;
  band: string | null;
  effective_date: string | null;
}

export const buyItem = (l: BuyLine) => l.catalog_name || l.label;

/** `back`: where "Quay lại" on the sent screen returns to (e.g. the job wizard). */
export const buyLink = (r: BuyRequest, l: BuyLine, back?: string) =>
  `/workload/purchases/new?${new URLSearchParams({
    source: "onboard",
    ref: r.display_id ?? r.id,
    kind: "new",
    item: buyItem(l),
    catalog: l.catalog_name,
    qty: String(l.buy),
    employee: `${r.full_name ?? ""}${r.employee_id ? ` (${r.employee_id})` : ""}`,
    reason: `Onboard ${r.full_name ?? ""} – band ${r.band ?? "?"}, hiệu lực ${formatDate(r.effective_date)}: kho không còn ${l.label} phù hợp tiêu chuẩn.`,
    ...(back ? { back } : {}),
  })}`;

/** The proposal already made for this line of this request, if any (rejected ones do not count). */
export const proposedFor = <P extends { source_ref: string | null; item: string; status: string }>(
  purchases: P[] | undefined | null,
  r: BuyRequest,
  l: BuyLine,
) => purchases?.find((p) => p.source_ref === (r.display_id ?? r.id) && p.item === buyItem(l) && p.status !== "rejected");
