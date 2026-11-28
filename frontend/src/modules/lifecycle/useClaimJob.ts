import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import type { Job } from "./jobs";

/** A hand-over of a job (to the claimer or by a coordinator) that the worker is writing to SDP, or that failed. */
export interface Assignment {
  pending: boolean;
  id?: string;
  status?: string;
  to_name?: string | null;
  /** The hand-over is to the person looking at it. */
  mine?: boolean;
  /** "personal_key": the requester has not entered their own SDP key, the worker waits for it. */
  waiting_reason?: "personal_key" | null;
  requested_by_name?: string | null;
  message?: string | null;
}

const assigneeKey = (jobId: string, qc: ReturnType<typeof useQueryClient>) => {
  const a = qc.getQueryData<Job>(["job", jobId])?.assignee;
  return a ? `${a.user_id ?? ""}|${a.name}` : "";
};

/** Polls while a hand-over runs (4 s; 30 s while it waits for a personal key) and stops once it failed, finished or
 * the job's assignee changed. Refreshes the job so the new assignee shows up. */
export function useAssignment(jobId: string) {
  const qc = useQueryClient();
  const started = useRef<string | null>(null); // the assignee when the pending hand-over was first seen
  return useQuery({
    queryKey: ["jobs", "assign", jobId],
    queryFn: async () => {
      const prev = qc.getQueryData<Assignment>(["jobs", "assign", jobId]);
      const next = await api<Assignment>(`/api/lifecycle/jobs/${jobId}/assignment`);
      if (!next.pending) started.current = null;
      else if (started.current === null) started.current = assigneeKey(jobId, qc);
      if (prev?.pending || next.pending) void qc.invalidateQueries({ queryKey: ["job", jobId] });
      return next;
    },
    refetchInterval: (q) => {
      const d = q.state.data;
      if (!d?.pending || (started.current !== null && started.current !== assigneeKey(jobId, qc))) return false;
      return d.status === "waiting" ? 30_000 : 4000;
    },
  });
}

/** "Nhận Job": shared by the full job page, the wizard and the team queue (which passes the job id per call). */
export function useClaimJob(jobId?: string, onClaimed?: (id: string) => void) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id?: string) => api(`/api/lifecycle/jobs/${id ?? jobId}/claim`, { method: "POST" }),
    onSuccess: (_r, id) => {
      const target = id ?? jobId!;
      if (onClaimed) onClaimed(target);
      else toast.success("Đã nhận Job: infra-ops ghi bạn là Technician của request trên SDP trong ít giây.");
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      void qc.invalidateQueries({ queryKey: ["job", target] });
    },
    onError: (e, id) => {
      toast.error(errorMessage(e));
      void qc.invalidateQueries({ queryKey: ["jobs", "team"] });
      void qc.invalidateQueries({ queryKey: ["jobs", "assign", id ?? jobId] });
    },
  });
}
