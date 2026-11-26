import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowRightLeft } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Select, Textarea } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import type { Job } from "../jobs";

interface AssignJobRun {
  id: string;
  status: string;
  subject_label: string;
  steps: { key: string; message: string }[];
}

/** Hand a job to another operator. The worker sets the request's technician on SDP first. */
export function AssignJobButton({ job }: { job: Job }) {
  const qc = useQueryClient();
  const [open, setOpen] = useState(false);
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const people = useQuery({
    queryKey: ["lifecycle", "assignees"],
    queryFn: () => api<{ id: string; name: string; upn: string }[]>("/api/lifecycle/assignees"),
    enabled: open,
  });
  const send = useMutation({
    mutationFn: () => api(`/api/lifecycle/jobs/${job.id}/assign`, { method: "POST", json: { user_id: to, reason } }),
    onSuccess: () => {
      toast.success("Đang chuyển Job: infra-ops đổi Technician của request trên SDP trong ít giây.");
      setOpen(false);
      void qc.invalidateQueries({ queryKey: ["jobs", "assign", job.id] });
    },
  });
  const options = (people.data ?? []).filter((p) => p.id !== job.assignee?.user_id);
  const target = options.find((p) => p.id === to);
  return (
    <>
      <Button onClick={() => { setTo(""); setReason(""); setTried(false); setOpen(true); }}>
        <ArrowRightLeft className="size-4" aria-hidden="true" />Chuyển Job
      </Button>
      <Dialog
        open={open}
        onClose={() => setOpen(false)}
        title="Chuyển Job cho người khác"
        description={`Hiện do ${job.assignee?.name ?? "chưa ai"} phụ trách. Technician của request trên ServiceDesk Plus được đổi theo; lưu đồ ghi lại điểm chuyển giao.`}
        footer={
          <>
            <Button onClick={() => setOpen(false)}>Huỷ</Button>
            <Button variant="primary" loading={send.isPending} onClick={() => { setTried(true); if (to) send.mutate(); }}>
              {target ? `Chuyển cho ${target.name}` : "Chuyển Job"}
            </Button>
          </>
        }
      >
        <div className="space-y-3">
          <Field label="Người nhận" required error="Chọn người nhận." showError={tried && !to}>
            <Select data-autofocus value={to} onChange={(e) => setTo(e.target.value)}>
              <option value="">— Chọn Operator —</option>
              {options.map((p) => <option key={p.id} value={p.id}>{p.name} ({p.upn})</option>)}
            </Select>
          </Field>
          <Field label="Lý do" hint="Ví dụ: Nghỉ phép 24/11–28/11, chuyển cho người backup.">
            <Textarea rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
          </Field>
        </div>
      </Dialog>
    </>
  );
}

/** A hand-over still being written to SDP (or refused): never let it fail silently. */
export function PendingAssignment({ job }: { job: Job }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["jobs", "assign", job.id],
    queryFn: () => api<{ items: AssignJobRun[] }>(`/api/core/jobs?${new URLSearchParams({ workflow: "lifecycle.assign", status: "open", q: job.id })}`),
    refetchInterval: (query) => ((query.state.data?.items.length ?? 0) > 0 ? 4000 : false),
  });
  const retry = useMutation({
    mutationFn: (id: string) => api(`/api/core/jobs/${id}/steps/reassign/retry`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["jobs", "assign", job.id] }),
  });
  const runs = q.data?.items ?? [];
  if (!runs.length) return null;
  return (
    <div className="mb-3 space-y-1.5">
      {runs.map((r) => {
        const failed = r.status === "failed";
        return (
          <p key={r.id} role={failed ? "alert" : "status"} className={`flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm ${failed ? "border-failed bg-failed-soft" : "border-line bg-surface"}`}>
            <StatusBadge status={failed ? "failed" : "in_progress"} label={failed ? "Chuyển Job lỗi" : "Đang chuyển"} />
            {r.subject_label}
            {failed && <span className="basis-full text-failed">{r.steps[0]?.message}</span>}
            {failed && can("lifecycle.job.assign") && <Button size="sm" loading={retry.isPending} onClick={() => retry.mutate(r.id)}>Thử lại</Button>}
          </p>
        );
      })}
    </div>
  );
}
