import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/core/api/client";

export type TicketState = "open" | "pending" | "in_progress" | "on_hold" | "overdue" | "done" | "cancelled" | "all";
export type DeviceNeed = "replace" | "upgrade" | "repair";

export interface Ticket {
  id: string;
  display_id: string | null;
  subject: string;
  requester_name: string | null;
  technician_id: string | null;
  technician_name: string | null;
  group_name: string | null;
  status: string;
  state: "open" | "done" | "cancelled";
  phase: "pending" | "in_progress" | "on_hold" | null;
  device_need: DeviceNeed | null;
  po_dismissed: boolean;
  priority: string | null;
  created_at: string | null;
  due_by: string | null;
  completed_at: string | null;
  overdue: boolean;
}

export interface Technician {
  id: string;
  name: string;
  email: string | null;
  active: boolean;
  user: { id: string; upn: string; display_name: string } | null;
  mapped_manually: boolean;
  open: number;
  overdue: number;
}

export interface TechniciansResponse {
  items: Technician[];
  unassigned: { open: number; overdue: number };
  me: string | null;
  /** False for operators: the API only returns their own tasks and figures. */
  view_all: boolean;
}

export interface StatRow {
  received: number;
  completed: number;
  completed_late: number;
  completed_on_time: number;
  on_time_rate: number | null;
  avg_hours: number | null;
  open: number;
  overdue: number;
}

export interface Stats {
  from: string;
  to: string;
  bucket: "day" | "week" | "month";
  totals: StatRow;
  technicians: (StatRow & { technician_id: string | null; name: string | null })[];
  series: { period: string; received: number; completed: number }[];
}

/** Operators with an infra-ops account; `all` adds technicians not added to the app yet (settings only). */
export const useTechnicians = (all = false) =>
  useQuery({ queryKey: ["workload", "technicians", all], queryFn: () => api<TechniciansResponse>(`/api/workload/technicians${all ? "?all=1" : ""}`) });

export function useSyncNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api("/api/workload/sync", { method: "POST" }),
    onSuccess: () => {
      toast.success("Đã yêu cầu đồng bộ, dữ liệu sẽ cập nhật sau ít phút.");
      void qc.invalidateQueries({ queryKey: ["workload", "sync"] });
    },
  });
}

export const useSyncStatus = () =>
  useQuery({
    queryKey: ["workload", "sync"],
    queryFn: () => api<{ last_synced_at: string | null; task: { running: boolean; last_error: string | null } | null }>("/api/workload/sync"),
    refetchInterval: 30_000,
  });

export type PurchaseStatus = "proposed" | "approved" | "ordered" | "received" | "rejected";
export interface Evidence {
  id: string;
  filename: string;
  mime: string;
  created_by: string;
  created_at: string;
}
export interface Purchase {
  id: string;
  ticket_id: string | null;
  ticket_display_id: string | null;
  source: "ticket" | "onboard" | "manual";
  source_ref: string | null;
  employee: string | null;
  kind: DeviceNeed | "new";
  item: string;
  catalog_item: { id: string; name: string; category?: string } | null;
  quantity: number;
  unit_price: number | null;
  amount: number | null;
  reason: string;
  status: PurchaseStatus;
  note: string;
  created_by: string;
  /** Display name of the requester (falls back to the e-mail). */
  created_by_name: string;
  created_at: string;
  updated_at: string;
  approved_by: string | null;
  evidence: Evidence[];
  po: { id: string; number: string | null; status: "open" | "closed" } | null;
  next: PurchaseStatus[];
  sdp_product: string;
  received_assets: { id: string; serial: string; asset_tag: string | null }[];
  receive_job: { id: string; status: string; error: string | null } | null;
  /** Only for price viewers (supervisor/manager); empty/null for operators. */
  quotes: Quote[];
  quote_rule: { required: boolean; problem: string | null; threshold: number; count: number } | null;
}

export interface Quote {
  id: string;
  vendor: string;
  /** null when the vendor had no stock (file = screenshot of its web store). */
  unit_price: number | null;
  out_of_stock: boolean;
  mime: string;
  filename: string;
  lowest: boolean;
  created_by: string;
}

export interface CatalogItem {
  id: string;
  name: string;
  category: string;
  spec: string;
  // Price fields are omitted for operators (they request devices, not prices).
  unit_price?: number | null;
  sdp_product?: string;
  quote_file?: boolean;
  needs_quotes?: boolean;
  vendor?: string;
  quoted_at?: string | null;
  active: boolean;
  updated_by?: string | null;
  updated_at?: string;
}

export interface PurchaseOrder {
  id: string;
  number: string | null;
  status: "open" | "closed";
  total: number;
  limit: number;
  over_limit: boolean;
  created_at: string;
  closed_at: string | null;
  closed_by: string | null;
  items: Purchase[];
}

export const useCatalog = () =>
  useQuery({ queryKey: ["workload", "catalog"], queryFn: () => api<CatalogItem[]>("/api/workload/catalog") });

export interface MyWork {
  linked: boolean;
  technician?: string;
  counts?: { pending: number; in_progress: number; on_hold: number; overdue: number; done_7d: number };
  open?: Ticket[];
  done?: Ticket[];
  suggestions?: Ticket[];
}

export interface Supplier {
  id: string;
  name: string;
  keywords: string[];
  price_position: number;
  website: string;
  active: boolean;
  updated_by: string | null;
  updated_at: string;
}

export const useSuppliers = (enabled = true) =>
  useQuery({ queryKey: ["workload", "suppliers"], queryFn: () => api<Supplier[]>("/api/workload/suppliers"), enabled });

export type SlaGroup = "overdue" | "due_soon" | "on_track" | "on_hold" | "no_due";
export interface SlaBreakdownRow {
  key: string;
  label: string;
  overdue: number;
  due_soon: number;
  open: number;
}
export interface Sla {
  now: string;
  due_soon_hours: number;
  groups: Record<SlaGroup, number>;
  aging: { bucket: string; count: number }[];
  /** `technician` is absent for operators (they only see their own tasks). */
  by: { technician?: SlaBreakdownRow[]; group: SlaBreakdownRow[]; category: SlaBreakdownRow[]; priority: SlaBreakdownRow[] };
  items: (Ticket & { category: string | null })[];
}

export const useSla = (filters: { tech: string; group: string; category: string }) =>
  useQuery({
    queryKey: ["workload", "sla", filters],
    queryFn: () => api<Sla>(`/api/workload/sla?${new URLSearchParams(filters)}`),
  });
