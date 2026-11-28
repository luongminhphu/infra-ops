import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { AlertTriangle, ArrowRightLeft, Hand } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Select, Textarea } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import type { Job } from "../jobs";
import { useAssignment, useClaimJob, type Assignment } from "../useClaimJob";

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
  const q = useAssignment(job.id);
  const retry = useMutation({
    mutationFn: (id: string) => api(`/api/core/jobs/${id}/steps/reassign/retry`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["jobs", "assign", job.id] }),
  });
  const a = q.data;
  if (!a?.id) return null;
  const failed = a.status === "failed";
  return (
    <p className={`mb-3 flex flex-wrap items-center gap-2 rounded-md border px-3 py-2 text-sm ${failed ? "border-failed bg-failed-soft" : "border-line bg-surface"}`}>
      <StatusBadge status={failed ? "failed" : "in_progress"} label={failed ? "Chuyển Job lỗi" : "Đang chuyển"} />
      {a.to_name ? `Chuyển cho ${a.to_name}` : job.subject_label}
      {a.waiting_reason === "personal_key" && <span className="basis-full text-muted">{waitingText(a)}</span>}
      {failed && <span className="basis-full text-failed">{a.message}</span>}
      {failed && can("lifecycle.job.assign") && <Button size="sm" loading={retry.isPending} onClick={() => retry.mutate(a.id!)}>Thử lại</Button>}
    </p>
  );
}

const waitingText = (a: Assignment) => `Đang chờ khoá API của ${a.requested_by_name ?? "người yêu cầu"}`;
/** What the hand-over in progress is doing, from the point of view of whoever reads it. */
const pendingLabel = (a: Assignment | undefined) =>
  a?.waiting_reason === "personal_key"
    ? waitingText(a)
    : a?.mine === false
      ? `Đang chuyển cho ${a.to_name ?? "người khác"}…`
      : "Đang ghi bạn là người phụ trách lên SDP…";

/** Top of the wizard: take an unassigned job, or say who has it. The preparation steps stay usable below. */
export function ClaimCard({ job }: { job: Job }) {
  const { can } = useAuth();
  const claim = useClaimJob(job.id);
  const assignment = useAssignment(job.id);
  if (["completed", "cancelled"].includes(job.status)) return null;
  const a = assignment.data;
  if (job.assignee) {
    return job.assignee.user_id ? null : (
      <p className="mx-auto mb-3 max-w-3xl rounded-sm bg-surface-2 px-3 py-2 text-sm text-muted">Job do <strong className="font-medium text-ink">{job.assignee.name}</strong> phụ trách trên SDP.</p>
    );
  }
  if (!can("lifecycle.job.execute")) return null;
  const noKey = claim.error instanceof ApiError && claim.error.code === "personal_credential_missing";
  const pending = a?.pending || claim.isPending;
  const failed = a?.status === "failed";
  return (
    <>
      {/* The live region stays mounted so screen readers announce the text when it appears. */}
      <div role="status" className="mx-auto max-w-3xl">
        {pending && (
          <p className="mb-3 flex items-center gap-2 rounded-md border border-line bg-surface px-3 py-2 text-sm">
            <StatusBadge status="in_progress" label={pendingLabel(a)} />
          </p>
        )}
      </div>
      {!pending && (
    <section aria-labelledby={`claim-${job.id}`} className="mx-auto mb-3 flex max-w-3xl flex-wrap items-center justify-between gap-3 rounded-md border border-primary bg-primary-soft p-4">
      <div className="min-w-0 flex-1 basis-64">
        <h2 id={`claim-${job.id}`} className="text-base font-semibold">Job này chưa có người phụ trách</h2>
        <p className="text-sm text-muted">Khi nhận, bạn thành Technician của request trên SDP và Job vào “Việc của tôi”. Bạn vẫn chuẩn bị Job ở bên dưới trước khi nhận.</p>
        {failed && <p className="mt-1 flex items-center gap-1.5 text-sm text-failed"><AlertTriangle className="size-4" aria-hidden="true" />{a?.message}</p>}
        {noKey && (
          <p role="alert" className="mt-1 flex flex-wrap items-center gap-1.5 text-sm text-failed">
            <AlertTriangle className="size-4" aria-hidden="true" />Bạn chưa nhập khoá API ServiceDesk Plus.
            <Link to="/me/credentials" className="font-medium text-primary underline">Khoá API của tôi</Link>
          </p>
        )}
      </div>
      <Button variant="primary" onClick={() => claim.mutate()}>
        <Hand className="size-4" aria-hidden="true" />{failed ? "Thử lại" : "Nhận Job"}
      </Button>
    </section>
      )}
    </>
  );
}
