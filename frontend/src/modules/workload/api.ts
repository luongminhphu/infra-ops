import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";

export type TicketState = "open" | "overdue" | "done" | "cancelled" | "all";

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

export const useTechnicians = () =>
  useQuery({ queryKey: ["workload", "technicians"], queryFn: () => api<TechniciansResponse>("/api/workload/technicians") });

export function useSyncNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api("/api/workload/sync", { method: "POST" }),
    onSuccess: () => {
      toast.success("Đã yêu cầu đồng bộ, dữ liệu sẽ cập nhật sau ít phút.");
      void qc.invalidateQueries({ queryKey: ["workload", "sync"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
}

export const useSyncStatus = () =>
  useQuery({
    queryKey: ["workload", "sync"],
    queryFn: () => api<{ last_synced_at: string | null; task: { running: boolean; last_error: string | null } | null }>("/api/workload/sync"),
    refetchInterval: 30_000,
  });
