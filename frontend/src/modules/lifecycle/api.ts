import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/core/api/client";

export type Kind = "onboard" | "probation_to_official" | "transfer" | "offboard" | "ignore";
export type Severity = "high" | "medium" | "low";
export type CellStatus = "match" | "mismatch" | "pending" | "skipped" | "warning" | "failed";

export interface Cell {
  status: CellStatus;
  label: string;
  detail: string;
}

export interface Issue {
  rule: string;
  message: string;
  severity: Severity;
  system: "sdp" | "assets" | "biostar";
  acked?: boolean;
}

export interface ReconRow {
  employee_id: string;
  full_name: string;
  department: string | null;
  kind: Kind | null;
  effective_date: string | null;
  request_ids: string[];
  /** The request that decides kind/date: the only one a Job is created for (older ones may be long closed). */
  latest_request_id?: string | null;
  sdp_user_ids: string[];
  asset_ids: string[];
  biostar_user_id: string | null;
  cells: Record<"sdp" | "assets" | "biostar", Cell>;
  issues: Issue[];
  issue_count: number;
  max_severity: Severity | null;
}

export interface SyncRun {
  id: string;
  trigger: "manual" | "scheduled";
  requested_by: string | null;
  started_at: string;
  finished_at: string | null;
  status: "running" | "success" | "partial" | "failure";
  stats: Record<string, number>;
  errors: Record<string, string>;
  notes_count: number;
}

export interface Note {
  rule: string;
  message: string;
  request_id?: string;
  subject?: string;
  asset_tag?: string;
}

export interface ReconResponse {
  run: SyncRun | null;
  total: number;
  items: ReconRow[];
  facets: { rules?: Record<string, number>; departments?: string[]; severity?: Record<string, number>; total_rows?: number };
  notes: Note[];
}

export interface Rule {
  code: string;
  severity: Severity;
  system: string;
  title: string;
}

export interface TaskState {
  key: string;
  interval_seconds: number;
  enabled: boolean;
  next_run_at: string;
  running: boolean;
  last_started_at: string | null;
  last_finished_at: string | null;
  last_status: "running" | "success" | "failure" | null;
  last_error: string | null;
}

export interface RtrRequest {
  id: string;
  display_id: string | null;
  subject: string;
  status: string | null;
  created_at: string | null;
  employee_id: string | null;
  full_name: string | null;
  change_type_raw: string | null;
  kind: Kind | null;
  effective_date: string | null;
  department: string | null;
  parse_errors: string[];
  fields?: Record<string, string | null>;
  description_text?: string;
  field_titles?: Record<string, string>;
}

export function useRules() {
  return useQuery({ queryKey: ["lifecycle", "rules"], queryFn: () => api<Rule[]>("/api/lifecycle/rules"), staleTime: Infinity });
}

export function useSyncStatus(poll = false) {
  return useQuery({
    queryKey: ["lifecycle", "sync"],
    queryFn: () => api<{ task: TaskState | null; runs: SyncRun[] }>("/api/lifecycle/sync"),
    // Every 3 s while a sync runs or is due/requested; idle: 30 s, so a run someone else starts still shows up.
    refetchInterval: (q) => {
      const t = q.state.data?.task;
      if (!poll) return false;
      if (!t || t.running || Date.parse(t.next_run_at) <= Date.now()) return 3000;
      return 30_000;
    },
  });
}

/** Requests a sync from the worker and keeps polling until it finishes. */
export function useSyncNow() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api<TaskState>("/api/lifecycle/sync", { method: "POST" }),
    onSuccess: async () => {
      toast.info("Đã yêu cầu đồng bộ. Dữ liệu sẽ cập nhật khi worker chạy xong.");
      await qc.invalidateQueries({ queryKey: ["lifecycle", "sync"] });
    },
  });
}
