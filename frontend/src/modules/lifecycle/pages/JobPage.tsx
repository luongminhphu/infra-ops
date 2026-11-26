import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { JobWizard } from "./JobWizard";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CalendarClock, ListChecks, LoaderCircle, ChevronLeft, Eye, Play, RotateCcw, ShieldAlert, SkipForward, Workflow, XCircle } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { ConfirmDangerDialog } from "@/core/ui/confirm-danger-dialog";
import { DateInput } from "@/core/ui/date-input";
import { Dialog } from "@/core/ui/dialog";
import { ErrorState, LoadingRows, Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Textarea } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";
import { StatusBadge, StatusIcon } from "@/core/ui/status";
import { Flowchart } from "@/core/ui/flowchart";
import { Stepper } from "@/core/ui/stepper";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import { JOB_STATUS, STEP_STATUS, idleText, jobStatusLabel, SYSTEM_WRITE_LABEL, WORKFLOW_LABEL, useJob, type Job, type JobAsset, type JobStep } from "../jobs";
import { CollectAssetsForm, DocumentLink, DraftConfig, EnrollForm } from "./JobStepPanels";
import { StepVerification } from "./StepVerification";
import { AssignJobButton, PendingAssignment } from "./AssignJob";
import { Swimlane } from "@/core/ui/swimlane";

interface PreviewStep {
  key: string;
  title: string;
  status: string;
  writes: string[];
  lines: string[];
  error: string | null;
}

/**
 * One step of a job. `focus` (operator wizard): only what the person acts on now – verification once the
 * step has run, no timing details or before-state.
 */
export function StepDetail({ job, step, act, focus = false }: { job: Job; step: JobStep; act: ReturnType<typeof useJobActions>; focus?: boolean }) {
  const { can } = useAuth();
  const canRun = can("lifecycle.job.execute");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [skipOpen, setSkipOpen] = useState(false);
  const [reason, setReason] = useState("");
  const st = STEP_STATUS[step.status];
  const active = ["running", "waiting", "failed"].includes(job.status);
  const isCurrent = job.current_step === step.key;
  const awaitingConfirm = step.requires_confirm && !step.confirmed_at && step.status === "waiting";
  const isDoc = step.key === "handover_doc" || step.key === "return_doc";

  return (
    <Panel
      title={<span className="flex flex-wrap items-center gap-2">{step.title}<StatusBadge status={st.status} label={st.label} /></span>}
      description={step.description}
    >
      <div className="space-y-4">
        <div className="flex flex-wrap gap-2 text-sm">
          <span className="rounded-xs bg-surface-2 px-1.5 py-0.5">{step.kind === "manual" ? "Thủ công" : "Tự động"}</span>
          {step.writes.map((w) => (
            <span key={w} className="rounded-xs border border-line px-1.5 py-0.5">Ghi vào {SYSTEM_WRITE_LABEL[w] ?? w}</span>
          ))}
          {step.requires_confirm && <span className="rounded-xs bg-failed-soft px-1.5 py-0.5 text-failed">Cần nhập lại Mã NV</span>}
        </div>

        {step.message && !awaitingConfirm && (
          <p className={cn("rounded-sm px-3 py-2", step.status === "failed" ? "bg-failed-soft text-failed" : "bg-surface-2")} role={step.status === "failed" ? "alert" : undefined}>
            {step.message}
          </p>
        )}
        {!focus && isCurrent && job.status === "running" && step.kind === "auto" && ["pending", "in_progress"].includes(step.status) &&
          (job.stalled_minutes ? (
            <div role="alert" className="flex items-start gap-2 rounded-sm bg-waiting-soft px-3 py-2 text-sm text-waiting">
              <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              <span>
                <b className="font-semibold">Bước tự động chưa chạy sau {idleText(job.stalled_minutes)}.</b> Dịch vụ chạy nền (worker) có thể đang dừng.
                Nhờ quản trị kiểm tra worker; Job tự chạy tiếp khi worker hoạt động lại – không cần bỏ qua bước.
              </span>
            </div>
          ) : (
            <p className="flex items-center gap-2 text-sm text-progress">
              <LoaderCircle className="size-4 motion-safe:animate-spin" aria-hidden="true" />Hệ thống đang chạy bước này – thường xong trong vài giây, trang tự cập nhật.
            </p>
          ))}
        {step.status === "waiting" && step.next_run_at && step.kind === "auto" && !awaitingConfirm && (
          <p className="text-sm text-muted">Hệ thống tự kiểm tra lại lúc {formatDateTime(step.next_run_at)}.</p>
        )}

        {active && isCurrent && canRun && (
          <>
            {awaitingConfirm && (
              <div className="rounded-sm border border-failed/30 bg-failed-soft p-3">
                <p className="flex items-start gap-2 text-sm"><ShieldAlert className="mt-0.5 size-4 shrink-0 text-failed" aria-hidden="true" />{step.confirm_text || "Bước này cần xác nhận trước khi chạy."}</p>
                <Button variant="danger" className="mt-2" onClick={() => setConfirmOpen(true)}>Xác nhận và chạy bước này</Button>
              </div>
            )}
            {step.key === "collect_assets" && step.status === "waiting" && (
              <CollectAssetsForm step={step} busy={act.complete.isPending} onSubmit={(data) => act.complete.mutate({ key: step.key, data })} />
            )}
            {step.key === "biostar_enroll" && step.status === "waiting" && (
              <EnrollForm busy={act.complete.isPending} onSubmit={(data) => act.complete.mutate({ key: step.key, data })} />
            )}
            {step.kind === "manual" && step.status === "waiting" && !["collect_assets", "biostar_enroll"].includes(step.key) && (
              <Button variant="primary" loading={act.complete.isPending} onClick={() => act.complete.mutate({ key: step.key, data: {} })}>Đánh dấu hoàn tất</Button>
            )}
          </>
        )}
        {isDoc && <DocumentLink step={step} />}
        {(!focus || ["verify", "done"].includes(step.status)) && <StepVerification job={job} step={step} canRun={canRun} />}

        {canRun && active && (step.status === "failed" || (step.skippable && !job.stalled_minutes && !["done", "skipped", "verify"].includes(step.status))) && (
          <div className="flex flex-wrap gap-2 border-t border-line pt-3">
            {step.status === "failed" && (
              <Button onClick={() => act.retry.mutate(step.key)} loading={act.retry.isPending}>
                <RotateCcw className="size-4" aria-hidden="true" />Chạy lại bước
              </Button>
            )}
            {step.skippable && !job.stalled_minutes && !["done", "skipped", "verify"].includes(step.status) && (
              <Button variant="ghost" onClick={() => { setReason(""); setSkipOpen(true); }}>
                <SkipForward className="size-4" aria-hidden="true" />Bỏ qua bước
              </Button>
            )}
          </div>
        )}

        {!focus && (<>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
          {step.attempts > 0 && (<><dt className="text-muted">Số lần chạy</dt><dd className="tabular">{step.attempts}</dd></>)}
          {step.started_at && (<><dt className="text-muted">Bắt đầu</dt><dd>{formatDateTime(step.started_at)}</dd></>)}
          {step.finished_at && (<><dt className="text-muted">Thực hiện xong</dt><dd>{formatDateTime(step.finished_at)} · {step.actor?.startsWith("worker") ? "hệ thống" : step.actor}</dd></>)}
          {step.confirmed_at && (<><dt className="text-muted">Xác nhận bởi</dt><dd>{step.confirmed_by} · {formatDateTime(step.confirmed_at)}</dd></>)}
        </dl>
        {step.before_state && (
          <details>
            <summary className="cursor-pointer text-sm font-medium">Trạng thái trước khi thay đổi (để khôi phục thủ công)</summary>
            <pre className="mt-2 max-h-64 overflow-auto rounded-sm bg-surface-2 p-2 font-mono text-xs">{JSON.stringify(step.before_state, null, 2)}</pre>
          </details>
        )}
        </>)}
      </div>

      <ConfirmDangerDialog
        open={confirmOpen}
        onClose={() => setConfirmOpen(false)}
        onConfirm={() => act.confirm.mutate(step.key, { onSuccess: () => setConfirmOpen(false) })}
        loading={act.confirm.isPending}
        title={step.title}
        confirmText={job.subject_key}
        confirmLabel="Xác nhận và chạy"
        inputLabel={<>Nhập Mã NV <strong className="font-mono">{job.subject_key}</strong> để xác nhận</>}
        consequences={<><p>{step.confirm_text}</p><p>Nhân viên: {job.subject_label}.</p></>}
      />
      <Dialog
        open={skipOpen}
        onClose={() => setSkipOpen(false)}
        title={`Bỏ qua “${step.title}”?`}
        size="sm"
        footer={<><Button onClick={() => setSkipOpen(false)}>Không bỏ qua</Button><Button variant="primary" disabled={reason.trim().length < 3} loading={act.skip.isPending} onClick={() => act.skip.mutate({ key: step.key, reason }, { onSuccess: () => setSkipOpen(false) })}>Bỏ qua bước</Button></>}
      >
        <Field label="Lý do" required hint="Ghi vào nhật ký Job và Nhật ký kiểm tra.">
          <Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
        </Field>
      </Dialog>
    </Panel>
  );
}

export function useJobActions(job: Job | undefined) {
  const qc = useQueryClient();
  const id = job?.id;
  const done = (j: Job, text?: string) => {
    qc.setQueryData(["job", j.id], j);
    void qc.invalidateQueries({ queryKey: ["jobs"] });
    if (text) {
      toast.success(text);
      announce(text);
    }
  };
  const fail = (e: unknown) => {
    toast.error(errorMessage(e));
    announce(errorMessage(e));
    if (e instanceof ApiError && e.code === "stale_version") void qc.invalidateQueries({ queryKey: ["job", id] });
  };
  return {
    start: useMutation({
      mutationFn: (scheduled_for: string | null) => api<Job>(`/api/core/jobs/${id}/start`, { method: "POST", json: { version: job!.version, scheduled_for } }),
      onSuccess: (j) => done(j, j.status === "scheduled" ? `Đã lên lịch chạy ngày ${formatDate(j.scheduled_for)}.` : "Đã bắt đầu Job. Các bước tự động chạy trong giây lát."),
      onError: fail,
    }),
    params: useMutation({
      mutationFn: (patch: { assets?: JobAsset[]; sdp_user_id?: string; sdp_user_name?: string; clear_sdp_user?: boolean }) =>
        api<Job>(`/api/lifecycle/jobs/${id}/params`, { method: "PUT", json: { version: job!.version, ...patch } }),
      onSuccess: (j) => done(j),
      onError: fail,
    }),
    confirm: useMutation({
      mutationFn: (key: string) => api<Job>(`/api/core/jobs/${id}/steps/${key}/confirm`, { method: "POST", json: { typed: job!.subject_key } }),
      onSuccess: (j) => done(j, "Đã xác nhận. Bước sẽ chạy trong giây lát."),
      onError: fail,
    }),
    complete: useMutation({
      mutationFn: ({ key, data }: { key: string; data: Record<string, unknown> }) => api<Job>(`/api/core/jobs/${id}/steps/${key}/complete`, { method: "POST", json: { data } }),
      onSuccess: (j) => done(j, "Đã hoàn tất bước thủ công."),
      onError: fail,
    }),
    retry: useMutation({
      mutationFn: (key: string) => api<Job>(`/api/core/jobs/${id}/steps/${key}/retry`, { method: "POST" }),
      onSuccess: (j) => done(j, "Đã yêu cầu chạy lại bước."),
      onError: fail,
    }),
    skip: useMutation({
      mutationFn: ({ key, reason }: { key: string; reason: string }) => api<Job>(`/api/core/jobs/${id}/steps/${key}/skip`, { method: "POST", json: { reason } }),
      onSuccess: (j) => done(j, "Đã bỏ qua bước."),
      onError: fail,
    }),
    cancel: useMutation({
      mutationFn: (reason: string) => api<Job>(`/api/core/jobs/${id}/cancel`, { method: "POST", json: { reason } }),
      onSuccess: (j) => done(j, "Đã huỷ Job."),
      onError: fail,
    }),
    reopen: useMutation({
      mutationFn: (reason: string) => api<Job>(`/api/core/jobs/${id}/reopen`, { method: "POST", json: { reason } }),
      onSuccess: (j) => done(j, "Đã mở lại Job – Job về bản nháp."),
      onError: fail,
    }),
    override: useMutation({
      mutationFn: (reason: string) => api<Job>(`/api/core/jobs/${id}/override-close`, { method: "POST", json: { reason } }),
      onSuccess: (j) => done(j, "Đã buộc đóng Job."),
      onError: fail,
    }),
  };
}

export function JobPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const q = useJob(id);
  const job = q.data;
  const act = useJobActions(job);
  const [search, setSearch] = useSearchParams();
  // Operators work step by step by default; coordinators (who can reassign) get the full view.
  const view = search.get("view") ?? (can("lifecycle.job.assign") ? "full" : "wizard");
  const setView = (v: string) => setSearch((cur) => { const n = new URLSearchParams(cur); n.set("view", v); return n; }, { replace: true });
  const [selected, setSelected] = useState<string | null>(null);
  const [previewOpen, setPreviewOpen] = useState(false);
  const [flowOpen, setFlowOpen] = useState(false);
  const [scheduleOpen, setScheduleOpen] = useState(false);
  const [scheduleDate, setScheduleDate] = useState("");
  const [closeMode, setCloseMode] = useState<"cancel" | "override" | "reopen" | null>(null);
  const [reason, setReason] = useState("");
  const preview = useQuery({
    queryKey: ["job-preview", id, job?.version],
    queryFn: () => api<PreviewStep[]>(`/api/core/jobs/${id}/preview`),
    enabled: previewOpen,
  });

  useEffect(() => {
    if (job && !selected) setSelected(job.current_step ?? job.steps.find((s) => !["done", "skipped"].includes(s.status))?.key ?? job.steps[0]?.key ?? null);
  }, [job, selected]);
  useEffect(() => {
    if (job?.current_step) setSelected(job.current_step);
  }, [job?.current_step]);

  const back = (
    <Link to="/lifecycle/jobs" className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
      <ChevronLeft className="size-4" aria-hidden="true" />Job onboard / offboard
    </Link>
  );
  if (q.isLoading) return <>{back}<LoadingRows rows={8} /></>;
  if (q.isError || !job) return <>{back}<ErrorState error={q.error} onRetry={() => q.refetch()} /></>;

  const js = job.stalled_minutes ? { status: "warning" as const, label: "" } : JOB_STATUS[job.status];
  const canRun = can("lifecycle.job.execute");
  if (view === "wizard") return <>{back}<JobWizard job={job} onFull={() => setView("full")} /></>;
  const step = job.steps.find((s) => s.key === selected) ?? job.steps[0]!;
  const finished = ["completed", "cancelled"].includes(job.status);
  const eff = job.params.effective_date;

  return (
    <>
      <nav aria-label="Đường dẫn">{back}</nav>
      <PageHeader
        title={`${WORKFLOW_LABEL[job.workflow_key] ?? job.workflow_title} – ${job.subject_label}`}
        description={
          // Separate items (no leading "·" when the line wraps on a phone).
          <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
            <StatusBadge status={js.status} label={jobStatusLabel(job)} />
            <span className="font-medium text-ink tabular">
              {job.current_step ? `Đang ở bước ${job.steps.find((s) => s.key === job.current_step)?.seq}/${job.progress.total}` : `${job.progress.total} bước`} · xong {job.progress.done}
            </span>
            <span>Phụ trách: <strong className="font-medium text-ink">{job.assignee?.name ?? "chưa có"}</strong></span>
            <span>Request <Mono>#{job.params.request_display_id ?? job.external_ref}</Mono></span>
            <span>Hiệu lực {formatDate(eff)}</span>
            {job.scheduled_for && job.status === "scheduled" && <span>Chạy ngày {formatDate(job.scheduled_for)}</span>}
            {job.started_by && <span>Bắt đầu bởi {job.started_by}</span>}
          </span>
        }
        actions={
          <>
            {canRun && !finished && <Button onClick={() => setView("wizard")}><ListChecks className="size-4" aria-hidden="true" />Làm từng bước</Button>}
            <Button onClick={() => setFlowOpen(true)}><Workflow className="size-4" aria-hidden="true" />Lưu đồ</Button>
            <Button onClick={() => setPreviewOpen(true)}><Eye className="size-4" aria-hidden="true" />Xem trước các bước</Button>
            {can("lifecycle.job.assign") && !finished && <AssignJobButton job={job} />}
            {canRun && ["draft", "scheduled"].includes(job.status) && (
              <>
                {job.status === "draft" && (
                  <Button onClick={() => { setScheduleDate(eff ?? ""); setScheduleOpen(true); }}>
                    <CalendarClock className="size-4" aria-hidden="true" />Lên lịch
                  </Button>
                )}
                <Button variant="primary" loading={act.start.isPending} onClick={() => act.start.mutate(null)}>
                  <Play className="size-4" aria-hidden="true" />Bắt đầu ngay
                </Button>
              </>
            )}
          </>
        }
      />

      <PendingAssignment job={job} />
      {job.close_reason && (
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2 rounded-sm bg-surface-2 px-3 py-2 text-sm">
          <span>{job.status === "cancelled" ? "Đã huỷ" : "Ghi chú đóng"}: {job.close_reason}</span>
          {job.status === "cancelled" && canRun && (
            <Button size="sm" onClick={() => { setReason(""); setCloseMode("reopen"); }}>
              <RotateCcw className="size-4" aria-hidden="true" />Mở lại Job
            </Button>
          )}
        </div>
      )}

      {job.status === "draft" && job.params.kind === "onboard" && canRun && (
        <Panel title="Chuẩn bị trước khi bắt đầu" className="mb-4" description="Chỉ sửa được khi Job còn là bản nháp. Bản nháp không thay đổi gì trên SDP hay BioStar.">
          <DraftConfig job={job} saving={act.params.isPending} onSave={(p) => act.params.mutate(p)} />
        </Panel>
      )}

      <div className="grid gap-4 lg:grid-cols-[320px_minmax(0,1fr)]">
        {/* On phones the current step (what to do now) comes before the list of steps. */}
        <Panel title={`Các bước · xong ${job.progress.done}/${job.progress.total}`} bodyClassName="p-2" className="order-2 lg:order-none">
          <Stepper
            label="Các bước của Job"
            selected={selected}
            onSelect={setSelected}
            steps={job.steps.map((s) => ({
              key: s.key,
              title: s.title,
              status: STEP_STATUS[s.status].status,
              statusLabel: s.requires_confirm && !s.confirmed_at && s.status === "waiting" ? "Chờ xác nhận Mã NV" : s.kind === "manual" && s.status === "waiting" ? "Chờ Operator thực hiện" : s.status === "verify" ? "Chờ Operator kiểm tra" : STEP_STATUS[s.status].label,
              meta: s.finished_at ? formatDateTime(s.finished_at) : undefined,
            }))}
          />
        </Panel>
        <div className="order-1 min-w-0 space-y-4 lg:order-none">
          <StepDetail job={job} step={step} act={act} />
          <Panel title="Nhật ký Job" bodyClassName="p-0">
            {/* Scrollable region must be keyboard-focusable (WCAG 2.1.1). */}
            <ol className="max-h-80 divide-y divide-line overflow-y-auto" tabIndex={0} aria-label="Nhật ký Job">
              {(job.events ?? []).map((e, i) => (
                <li key={i} className="flex flex-wrap gap-x-3 gap-y-0.5 px-4 py-2 text-sm">
                  <StatusIcon status={e.level === "error" ? "failed" : e.level === "warn" ? "warning" : "done"} label={e.level === "error" ? "Lỗi" : e.level === "warn" ? "Cảnh báo" : "Thông tin"} />
                  <span className="min-w-0 flex-1">{e.message}</span>
                  <span className="shrink-0 text-muted tabular">{formatDateTime(e.at)}{e.actor ? ` · ${e.actor}` : ""}</span>
                </li>
              ))}
            </ol>
          </Panel>
          {canRun && !finished && (
            <details className="rounded-md border border-line bg-surface px-4 py-2.5 text-sm">
              <summary className="cursor-pointer font-medium">Kết thúc Job sớm</summary>
              <dl className="mt-3 grid gap-3 sm:grid-cols-2">
                {can("lifecycle.job.override_close") && job.status !== "draft" && (
                  <div>
                    <dt><Button size="sm" variant="danger" onClick={() => { setReason(""); setCloseMode("override"); }}>Buộc đóng Job</Button></dt>
                    <dd className="mt-1 text-muted">Đánh dấu hoàn tất dù còn bước chưa xong, khi phần còn lại đã xử lý ngoài infra-ops.</dd>
                  </div>
                )}
                <div>
                  <dt>
                    <Button size="sm" variant="danger" onClick={() => { setReason(""); setCloseMode("cancel"); }}>
                      <XCircle className="size-4" aria-hidden="true" />Huỷ Job
                    </Button>
                  </dt>
                  <dd className="mt-1 text-muted">Dừng hẳn, các bước còn lại không chạy. Thay đổi đã ghi lên SDP/BioStar giữ nguyên.</dd>
                </div>
              </dl>
            </details>
          )}
        </div>
      </div>

      <Dialog open={flowOpen} onClose={() => setFlowOpen(false)} title={`Lưu đồ ${job.workflow_title}`} description="Mỗi làn là một người phụ trách; mũi tên nhảy làn là lúc Job được chuyển cho người khác." size="xl">
        <Swimlane
          label={`Lưu đồ ${job.workflow_title} – ${job.subject_label}`}
          handoffs={job.assignments}
          steps={job.steps.map((s) => ({
            key: s.key,
            title: s.title,
            kind: s.kind,
            status: STEP_STATUS[s.status].status,
            statusLabel: STEP_STATUS[s.status].label,
            current: s.key === job.current_step && !finished,
          }))}
        />
        <details className="mt-4">
          <summary className="cursor-pointer text-sm font-medium">
            Checklist từng bước{job.checklist_version === null ? "" : ` (${job.checklist_version === 0 ? "checklist mặc định" : `phiên bản ${job.checklist_version}`}, cố định từ lúc bắt đầu Job)`}
          </summary>
          <div className="mt-3">
        <Flowchart
          label={`Lưu đồ ${job.workflow_title} – ${job.subject_label}`}
          verify={job.checklist_version !== null}
          systemLabel={SYSTEM_WRITE_LABEL}
          steps={job.steps.map((s) => ({
            key: s.key,
            title: s.title,
            kind: s.kind,
            writes: s.writes,
            confirm: s.requires_confirm,
            checklist: s.checklist ?? [],
            evidenceRequired: s.evidence_required,
            status: STEP_STATUS[s.status].status,
            statusLabel: STEP_STATUS[s.status].label,
          }))}
        />
          </div>
        </details>
      </Dialog>

      <Dialog open={previewOpen} onClose={() => setPreviewOpen(false)} title="Xem trước các bước (không thay đổi dữ liệu)" size="lg">
        {preview.isLoading && <LoadingRows rows={5} label="Đang đọc trạng thái hiện tại" />}
        {preview.isError && <ErrorState error={preview.error} />}
        <ol className="space-y-3">
          {preview.data?.map((p, i) => (
            <li key={p.key} className="rounded-sm border border-line p-3">
              <p className="flex flex-wrap items-center gap-2 font-medium">
                <span className="text-muted tabular">{i + 1}.</span>{p.title}
                {p.writes.map((w) => <span key={w} className="rounded-xs bg-waiting-soft px-1.5 text-sm font-normal text-waiting">Ghi vào {SYSTEM_WRITE_LABEL[w] ?? w}</span>)}
              </p>
              <ul className="mt-1 list-disc pl-5 text-sm">{p.lines.map((l, j) => <li key={j}>{l}</li>)}</ul>
              {p.error && <p className="mt-1 text-sm text-failed">{p.error}</p>}
            </li>
          ))}
        </ol>
      </Dialog>

      <Dialog
        open={scheduleOpen}
        onClose={() => setScheduleOpen(false)}
        title="Lên lịch chạy Job"
        size="sm"
        footer={<><Button onClick={() => setScheduleOpen(false)}>Huỷ</Button><Button variant="primary" disabled={!scheduleDate} loading={act.start.isPending} onClick={() => act.start.mutate(scheduleDate, { onSuccess: () => setScheduleOpen(false) })}>Lên lịch</Button></>}
      >
        <p className="mb-3 text-sm">Job tự bắt đầu vào 00:00 ngày đã chọn (giờ Việt Nam). Mặc định là ngày hiệu lực của request.</p>
        <Field label="Ngày chạy" required>
          <DateInput value={scheduleDate} onChange={setScheduleDate} />
        </Field>
      </Dialog>

      <Dialog
        open={closeMode !== null}
        onClose={() => setCloseMode(null)}
        title={closeMode === "cancel" ? "Huỷ Job?" : closeMode === "reopen" ? "Mở lại Job?" : "Buộc đóng Job?"}
        size="sm"
        footer={
          <>
            <Button onClick={() => setCloseMode(null)}>Giữ nguyên</Button>
            <Button
              variant={closeMode === "reopen" ? "primary" : "danger"}
              disabled={reason.trim().length < 3}
              loading={act.cancel.isPending || act.override.isPending || act.reopen.isPending}
              onClick={() => act[closeMode ?? "cancel"].mutate(reason, { onSuccess: () => setCloseMode(null) })}
            >
              {closeMode === "cancel" ? "Huỷ Job" : closeMode === "reopen" ? "Mở lại Job" : "Buộc đóng Job"}
            </Button>
          </>
        }
      >
        <p className="mb-3 text-sm">
          {closeMode === "reopen"
            ? "Job quay về bản nháp, các bước trở về “Chưa chạy” để làm lại từ đầu. Chỉ mở lại được khi chưa có bước nào ghi lên SDP/BioStar hoặc đã làm tay; nếu đã có, hãy tạo Job mới."
            : closeMode === "cancel"
            ? "Các bước chưa chạy sẽ không chạy nữa. Những thay đổi đã thực hiện trên SDP/BioStar giữ nguyên (xem “Trạng thái trước khi thay đổi” ở từng bước để khôi phục thủ công)."
            : "Job được đánh dấu hoàn tất dù còn bước chưa xong (ví dụ chưa có biên bản đã ký). Chỉ dùng khi đã xử lý bên ngoài infra-ops."}
        </p>
        <Field label="Lý do" required hint="Ghi vào nhật ký Job và Nhật ký kiểm tra.">
          <Textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} maxLength={500} />
        </Field>
      </Dialog>
    </>
  );
}
