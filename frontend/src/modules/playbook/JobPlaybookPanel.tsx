import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpen, Check, ClipboardList, CornerDownRight, Flag, GitBranch, RefreshCw, Undo2, Workflow } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDateTime } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Textarea } from "@/core/ui/field";
import { ErrorState } from "@/core/ui/feedback";
import { StatusBadge, type Status } from "@/core/ui/status";
import { OP_LABEL, OUTCOME, jobPlaybookKey, type JobPlaybook, type JobTraceEntry, type TraceValue } from "./api";

// The Job step statuses as the lifecycle module words them (same vocabulary, kept here so modules stay apart).
const JOB_STEP: Record<string, { label: string; status: Status }> = {
  pending: { label: "Chưa chạy", status: "pending" },
  in_progress: { label: "Đang chạy", status: "in_progress" },
  waiting: { label: "Đang chờ", status: "waiting" },
  verify: { label: "Chờ Operator kiểm tra", status: "waiting" },
  done: { label: "Hoàn tất", status: "done" },
  failed: { label: "Lỗi", status: "failed" },
  skipped: { label: "Bỏ qua", status: "skipped" },
};
const show = (v: TraceValue | null | undefined) => (v ? `${v.value}` : "—");
const isChoice = (e: JobTraceEntry) => !!e.options && e.kind !== "check" && e.kind !== "switch";
// Before the Job runs, open manual steps are expected: neutral tone, not a warning.
const NOT_STARTED = new Set(["draft", "scheduled"]);
const UNKNOWN_JOB_STEP = { label: "Không rõ", status: "pending" as Status };

function kindOf(e: JobTraceEntry): { icon: typeof ClipboardList; label: string } {
  if (e.kind === "job") return { icon: Workflow, label: "Bước của Job" };
  if (isChoice(e)) return { icon: GitBranch, label: "Chọn nhánh" };
  if (e.kind === "check") return { icon: GitBranch, label: "Kiểm tra tự động" };
  if (e.kind === "switch") return { icon: GitBranch, label: "Rẽ nhánh tự động" };
  if (e.kind === "outcome") return { icon: Flag, label: "Kết luận" };
  return { icon: ClipboardList, label: "Bước làm tay" };
}

/** The Playbook mapped to this Job: the path walked for its employee, Job steps live, manual steps ticked per Job. */
export function JobPlaybookPanel({ jobId, status, version, collapsible = false }: { jobId: string; workflowKey: string; status: string; version: number; collapsible?: boolean }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const key = jobPlaybookKey(jobId);
  // The Job's version changes with every step update, so Job step states in the panel follow without polling.
  const q = useQuery({ queryKey: [...key, version], queryFn: () => api<JobPlaybook>(`/api/playbook/jobs/${jobId}`), placeholderData: keepPreviousData });
  const [naFor, setNaFor] = useState<string | null>(null);
  const [reason, setReason] = useState("");
  const [tried, setTried] = useState(false);
  const d = q.data;
  const guide = d?.guide;
  const send = useMutation({
    mutationFn: (r: { node?: string; method: "PUT" | "DELETE"; path: string; json?: Record<string, unknown> }) => {
      const base = { guide_id: guide!.id, version: guide!.version };
      const url = `/api/playbook/jobs/${jobId}/${r.path}`;
      return r.method === "PUT"
        ? api<JobPlaybook>(url, { method: "PUT", json: { ...base, ...r.json } })
        : api<JobPlaybook>(`${url}?guide_id=${base.guide_id}&version=${base.version}`, { method: "DELETE" });
    },
    onSuccess: (next, r) => {
      qc.setQueriesData({ queryKey: key }, next);
      void qc.invalidateQueries({ queryKey: key });
      // Keep keyboard / screen-reader users on the step they just changed.
      if (r.node) setTimeout(() => document.getElementById(`pb-${jobId}-${r.node}`)?.focus(), 0);
    },
    onError: (e) => {
      if (e instanceof ApiError && e.code === "playbook_changed") {
        toast.error("Playbook vừa đổi – đã tải lại.");
        void qc.invalidateQueries({ queryKey: key });
      } else toast.error(errorMessage(e));
    },
  });

  if (q.isError && !d && !(q.error instanceof ApiError && (q.error.status === 403 || q.error.status === 404))) {
    return <Shell collapsible={collapsible} title="Playbook"><ErrorState error={q.error} onRetry={() => void q.refetch()} /></Shell>;
  }
  if (!d || (!guide && !d.ambiguous?.length)) return null;
  if (!guide) {
    return (
      <Shell collapsible={collapsible} title="Playbook">
        <p className="text-sm">
          Có nhiều Playbook phù hợp với quy trình này ({d.ambiguous!.map((g) => g.title).join(", ")}).
          {can("playbook.guides.publish") && <> <Link to="/playbook/job-guides" className="font-medium text-primary hover:underline">Gán Playbook cho quy trình</Link> để Job hiện đúng một Playbook.</>}
        </p>
      </Shell>
    );
  }

  const run = d.run!;
  const edit = !!d.can_edit;
  const last = run.trace.at(-1);
  const markNa = () => {
    setTried(true);
    if (reason.trim().length < 3) return;
    send.mutate({ method: "PUT", node: naFor!, path: `marks/${naFor}`, json: { status: "na", reason: reason.trim() } }, { onSuccess: () => setNaFor(null) });
  };
  const pick = (id: string, index: number) => send.mutate({ node: id, method: "PUT", path: `choices/${id}`, json: { index } });
  const clear = (node: string, path: string) => send.mutate({ node, method: "DELETE", path });

  return (
    <Shell collapsible={collapsible} title={`Playbook: ${guide.title}`} meta={`phiên bản ${guide.version}`} summary={`${run.manual_left > 0 ? `còn ${run.manual_left} bước tay` : "đã xong bước tay"} · ${run.path_complete ? "đã đi hết nhánh" : `đang ở “${last?.title ?? "—"}”`}`}>
      <div className="space-y-2">
        <p aria-live="polite">
          {run.manual_left > 0 && <StatusBadge status={NOT_STARTED.has(status) ? "pending" : "warning"} label={`Còn ${run.manual_left} bước tay chưa xong`} />}
        </p>
        {!run.path_complete && (
          <p className="text-sm text-muted">
            {run.status === "needs_choice" ? `Chưa đi hết nhánh – chọn tiếp ở bước “${last?.title}”.` : run.status === "missing_data" ? `Chưa đủ dữ liệu để đi tiếp ở bước “${last?.title}”.` : `Chưa đi hết nhánh – dừng ở bước “${last?.title}”.`}
          </p>
        )}
        {run.status === "outcome" && run.outcome && (
          <p className="flex flex-wrap items-center gap-2">
            <StatusBadge status={OUTCOME[run.outcome.result].status} label={`Kết luận: ${run.outcome.label}`} />
            {run.outcome.approval && <span className="text-sm">{run.outcome.approval}</span>}
          </p>
        )}
      </div>
      <ol className="mt-3 space-y-3 border-l border-line pl-4 text-sm">
        {run.trace.map((e, i) => {
          const k = kindOf(e);
          const Icon = k.icon;
          const chosen = e.chosen !== undefined ? e.options?.find((o) => o.index === e.chosen) : undefined;
          // In a loop the same node is visited again: do not offer the branch already taken on an earlier visit.
          const taken = new Set(run.trace.slice(0, i).filter((p) => p.id === e.id && p.chosen !== undefined).map((p) => p.chosen));
          const name = e.kind === "job" && e.job_title ? e.job_title : e.title;
          const act = (what: string) => `${what} – ${name}`;
          return (
            <li key={`${e.id}-${i}`}>
              <p id={`pb-${jobId}-${e.id}`} tabIndex={-1} className="flex flex-wrap items-center gap-x-2 gap-y-1 outline-none focus-visible:ring-2 focus-visible:ring-primary">
                <Icon className="size-4 shrink-0 text-muted" aria-hidden="true" />
                <span className="text-xs text-muted">{k.label}</span>
                <span className="font-medium">{name}</span>
              </p>
              <div className="mt-1 pl-6">
                {e.kind === "job" && (e.job_state === "not_applicable" ? <StatusBadge status="skipped" label="Không áp dụng cho Job này" /> : <StatusBadge status={(JOB_STEP[e.job_state ?? ""] ?? UNKNOWN_JOB_STEP).status} label={(JOB_STEP[e.job_state ?? ""] ?? UNKNOWN_JOB_STEP).label} />)}
                {e.manual && (
                  <div className="flex flex-wrap items-center gap-2">
                    {e.mark?.status === "done" && <StatusBadge status="done" label="Đã xong" />}
                    {e.mark?.status === "na" && <StatusBadge status="skipped" label="Không áp dụng" />}
                    {e.mark && <span className="text-muted">{e.mark.status === "na" && e.mark.reason ? `${e.mark.reason} · ` : ""}{e.mark.by ?? ""} · {formatDateTime(e.mark.at)}</span>}
                    {edit && !e.mark && (
                      <>
                        <Button size="sm" className="max-sm:h-11" aria-label={act("Đánh dấu xong")} disabled={send.isPending} onClick={() => send.mutate({ node: e.id, method: "PUT", path: `marks/${e.id}`, json: { status: "done" } })}><Check className="size-4" aria-hidden="true" />Đánh dấu xong</Button>
                        <Button size="sm" className="max-sm:h-11" aria-label={act("Không áp dụng")} disabled={send.isPending} onClick={() => { setReason(""); setTried(false); setNaFor(e.id); }}>Không áp dụng…</Button>
                      </>
                    )}
                    {edit && e.mark && <Button size="sm" className="max-sm:h-11" aria-label={act("Bỏ đánh dấu")} disabled={send.isPending} onClick={() => clear(e.id, `marks/${e.id}`)}><Undo2 className="size-4" aria-hidden="true" />Bỏ đánh dấu</Button>}
                  </div>
                )}
                {(e.kind === "check" || e.kind === "switch") && (
                  <p className="text-muted">
                    {e.left ? <>{e.left.label} = <b className="text-ink">{show(e.left)}</b></> : <i>{e.missing}: chưa có dữ liệu</i>}
                    {e.kind === "check" && <> {OP_LABEL[e.op!]} {e.right ? <>{e.right.label} = <b className="text-ink">{show(e.right)}</b></> : <i>{e.missing}: chưa có dữ liệu</i>}</>}
                    {e.result !== undefined && e.result !== null && <> → <b className={e.result ? "text-done" : "text-failed"}>{e.result ? "Đúng" : "Sai"}</b></>}
                    {e.kind === "switch" && e.chosen !== undefined && <> → nhánh “{chosen?.label ?? "đã chọn"}”</>}
                    {e.frozen_at && <span className="block text-xs">Đã chốt lúc {formatDateTime(e.frozen_at)}</span>}
                  </p>
                )}
                {(e.kind === "check" || e.kind === "switch") && e.frozen_at && edit && (
                  <Button size="sm" className="mt-1 max-sm:h-11" aria-label={act("Kiểm tra lại")} disabled={send.isPending} onClick={() => clear(e.id, `decisions/${e.id}`)}><RefreshCw className="size-4" aria-hidden="true" />Kiểm tra lại</Button>
                )}
                {isChoice(e) && chosen && (
                  <p className="flex flex-wrap items-center gap-2"><CornerDownRight className="size-4 text-muted" aria-hidden="true" />Đã chọn: <b>{chosen.label}</b>
                    {edit && <Button size="sm" className="max-sm:h-11" aria-label={act("Đổi nhánh")} disabled={send.isPending} onClick={() => clear(e.id, `choices/${e.id}`)}>Đổi</Button>}
                  </p>
                )}
                {isChoice(e) && e.chosen === undefined && (
                  edit ? (
                    <div className="flex flex-wrap gap-2">
                      {e.options!.filter((o) => !taken.has(o.index)).map((o) => <Button key={o.index} size="sm" className="max-sm:h-11" aria-label={act(o.label)} disabled={send.isPending} onClick={() => pick(e.id, o.index)}><CornerDownRight className="size-4" aria-hidden="true" />{o.label}</Button>)}
                    </div>
                  ) : <StatusBadge status="waiting" label="Chờ người phụ trách chọn nhánh" />
                )}
              </div>
            </li>
          );
        })}
      </ol>
      {run.off_path_marks.length > 0 && <p className="mt-3 text-xs text-muted">{run.off_path_marks.length} bước đã đánh dấu ở nhánh khác.</p>}
      <Dialog
        open={naFor !== null}
        onClose={() => setNaFor(null)}
        title="Không áp dụng bước này?"
        size="sm"
        footer={<><Button onClick={() => setNaFor(null)}>Huỷ</Button><Button variant="primary" loading={send.isPending} onClick={markNa}>Xác nhận</Button></>}
      >
        <Field label="Lý do" required error="Nhập lý do (từ 3 ký tự)." showError={tried && reason.trim().length < 3} hint="Ghi vào Nhật ký kiểm tra.">
          <Textarea data-autofocus rows={2} value={reason} maxLength={500} onChange={(ev) => setReason(ev.target.value)} />
        </Field>
      </Dialog>
    </Shell>
  );
}

function Shell({ collapsible, title, meta, summary, children }: { collapsible: boolean; title: string; meta?: string; summary?: string; children: ReactNode }) {
  const head = (
    <>
      <BookOpen className="size-4 shrink-0 text-primary" aria-hidden="true" />
      <span className="font-semibold">{title}</span>
      {meta && <span className="text-sm font-normal text-muted">{meta}</span>}
    </>
  );
  return collapsible ? (
    // The heading sits outside <summary>: VoiceOver flattens anything inside it into the button text.
    <div className="rounded-md border border-line bg-surface px-4 py-2.5">
      <h2 className="flex flex-wrap items-center gap-2 text-base">{head}</h2>
      <details>
        <summary className="cursor-pointer text-sm text-muted">{summary}</summary>
        <div className="mt-3">{children}</div>
      </details>
    </div>
  ) : (
    <section aria-label="Playbook" className="rounded-md border border-line bg-surface shadow-card">
      <h2 className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2.5 text-lg">{head}</h2>
      <div className="p-4">{children}</div>
    </section>
  );
}
