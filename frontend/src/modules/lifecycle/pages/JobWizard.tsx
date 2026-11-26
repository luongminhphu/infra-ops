import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, CheckCircle2, Coffee, Hand, LayoutList, Play, Plus, Recycle, ShoppingCart, Trash2, UserCheck } from "lucide-react";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDate } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { EmptyState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { From, Wizard, WizardProgress } from "@/core/ui/wizard";
import { JOB_STATUS, WORKFLOW_LABEL, jobStatusLabel, operatorTask, type Job, type JobAsset } from "../jobs";
import { StepDetail, useJobActions } from "./JobPage";
import { buyLink, proposedFor } from "../buy";
import { AssetPicker } from "./JobStepPanels";

interface Asset {
  id: string;
  name: string | null;
  asset_tag: string | null;
  product: string | null;
  product_type: string | null;
}
interface Line {
  label: string;
  quantity: number;
  catalog_name: string;
  picked: Asset[];
  reuse: Asset[];
  buy: number;
}
interface Prepare {
  request: {
    id: string;
    display_id: string | null;
    kind: string;
    full_name: string | null;
    employee_id: string | null;
    effective_date: string | null;
    band: string | null;
    band_source: "rtr" | "override" | null;
    details: { key: string; label: string; value: string }[];
  };
  sdp_user: { id: string; name: string; email: string | null; employee_id: string | null } | null;
  allocation: { status: string; lines: Line[] } | null;
}
interface PreviewStep {
  key: string;
  title: string;
  lines: string[];
  error: string | null;
}

const toJobAsset = (a: Asset): JobAsset => ({ id: a.id, name: a.name ?? a.id, tag: a.asset_tag, product_type: a.product_type });
const sameAssets = (a: JobAsset[], b: JobAsset[]) => a.map((x) => x.id).sort().join() === b.map((x) => x.id).sort().join();

/**
 * Operator's step-by-step view of an onboard/offboard job. Preparation asks only for confirmations:
 * everything the system already knows (RTR details, SDP user by employee ID, devices from stock) is
 * pre-filled. Once started, it shows only the current step and what to do in it.
 */
export function JobWizard({ job, onFull }: { job: Job; onFull: () => void }) {
  const { can } = useAuth();
  const act = useJobActions(job);
  const canRun = can("lifecycle.job.execute");
  const ref = job.params.request_display_id ?? job.external_ref;
  const header = (
    <PageHeader
      title={`${WORKFLOW_LABEL[job.workflow_key] ?? job.workflow_title} – ${job.subject_label}`}
      description={
        <span className="inline-flex flex-wrap items-center gap-x-3 gap-y-1">
          <StatusBadge status={job.stalled_minutes ? "warning" : JOB_STATUS[job.status].status} label={jobStatusLabel(job)} />
          <span>Request <Mono>#{ref}</Mono></span>
          <span>Hiệu lực {formatDate(job.params.effective_date)}</span>
        </span>
      }
      actions={
        <>
        <Link to="/me/work" className="inline-flex h-8 items-center gap-1.5 rounded-sm px-3 font-medium text-primary hover:bg-surface-2">Việc tiếp theo</Link>
        <Button onClick={onFull}>
          <LayoutList className="size-4" aria-hidden="true" />Xem chi tiết đầy đủ
        </Button>
        </>
      }
    />
  );

  if (job.status === "draft") return <>{header}<PrepareWizard job={job} act={act} canRun={canRun} /></>;

  if (["completed", "cancelled"].includes(job.status)) {
    return (
      <>
        {header}
        <div className="mx-auto max-w-3xl rounded-md border border-line bg-surface p-6 shadow-card">
          <EmptyState icon={CheckCircle2} title={job.status === "completed" ? "Job đã hoàn tất" : "Job đã huỷ"}>
            {job.close_reason ?? "Không còn việc cần làm. Xem chi tiết đầy đủ để đọc nhật ký và biên bản."}
          </EmptyState>
        </div>
      </>
    );
  }

  if (job.status === "scheduled") {
    return (
      <>
        {header}
        <div className="mx-auto max-w-3xl rounded-md border border-line bg-surface p-6 shadow-card">
          <EmptyState icon={CalendarClock} title={`Đã lên lịch chạy ngày ${formatDate(job.scheduled_for)}`}>
            Hệ thống tự bắt đầu vào ngày này. Bạn sẽ thấy bước cần làm tại đây và ở Việc của tôi.
          </EmptyState>
        </div>
      </>
    );
  }

  // Running / waiting / failed: one step at a time — the current one.
  const idx = Math.max(0, job.steps.findIndex((s) => s.key === job.current_step));
  const step = job.steps[idx]!;
  const task = operatorTask(job);
  return (
    <>
      {header}
      <section aria-label={`Bước hiện tại của ${job.subject_label}`} className="mx-auto w-full max-w-3xl">
        <WizardProgress steps={job.steps.map((s) => ({ key: s.key, title: s.title }))} current={idx} />
        <p role="status" className={`mb-3 flex items-start gap-2 rounded-sm px-3 py-2 font-medium ${task.act ? "bg-primary-soft text-ink" : "bg-surface-2 text-muted"}`}>
          {task.act ? <Hand className="mt-0.5 size-4 shrink-0 text-primary" aria-hidden="true" /> : <Coffee className="mt-0.5 size-4 shrink-0" aria-hidden="true" />}
          {task.text}
        </p>
        <StepDetail job={job} step={step} act={act} focus />
      </section>
    </>
  );
}

function PrepareWizard({ job, act, canRun }: { job: Job; act: ReturnType<typeof useJobActions>; canRun: boolean }) {
  const onboard = job.params.kind === "onboard";
  const steps = [
    { key: "info", title: "Thông tin nhân viên" },
    { key: "sdp", title: "User SDP" },
    ...(onboard ? [{ key: "devices", title: "Thiết bị cấp phát" }] : []),
    { key: "start", title: "Bắt đầu" },
  ];
  const [i, setI] = useState(0);
  const [userQ, setUserQ] = useState("");
  const [assets, setAssets] = useState<JobAsset[] | null>(null);
  const [pickerOpen, setPickerOpen] = useState(false);
  const prep = useQuery({ queryKey: ["job-prepare", job.id], queryFn: () => api<Prepare>(`/api/lifecycle/jobs/${job.id}/prepare`) });
  const users = useQuery({
    queryKey: ["sdp-users", userQ],
    queryFn: () => api<{ id: string; name: string; email: string | null; employee_id: string | null }[]>(`/api/lifecycle/sdp-users?q=${encodeURIComponent(userQ)}`),
    enabled: userQ.trim().length >= 2,
  });
  const { can } = useAuth();
  const purchases = useQuery({
    queryKey: ["workload", "purchases", "onboard-proposed"],
    queryFn: () => api<{ items: { source_ref: string | null; item: string; status: string }[] }>("/api/workload/purchases/onboard-proposed"),
    enabled: steps[i]?.key === "devices" && can("workload.tickets.view"),
  });
  const preview = useQuery({
    queryKey: ["job-preview", job.id, job.version],
    queryFn: () => api<PreviewStep[]>(`/api/core/jobs/${job.id}/preview`),
    enabled: steps[i]?.key === "start",
  });
  if (prep.isLoading || !prep.data) return <LoadingRows rows={6} />;
  const p = prep.data;
  const lines = p.allocation?.lines ?? [];
  const suggested = lines.flatMap((l) => [...l.picked, ...l.reuse]).map(toJobAsset);
  const current = job.params.assets ?? [];
  const chosen = assets ?? (current.length ? current : suggested);
  const go = (n: number) => setI(Math.max(0, Math.min(steps.length - 1, n)));
  const next = () => go(i + 1);
  const back = i > 0 ? () => go(i - 1) : undefined;
  const key = steps[i]!.key;
  const eff = job.params.effective_date;
  const future = !!eff && eff > new Date().toISOString().slice(0, 10);
  const readOnly = !canRun;

  if (key === "info") {
    return (
      <Wizard
        label="Chuẩn bị Job"
        steps={steps}
        current={i}
        title="Thông tin nhân viên có đúng không?"
        description={<>Lấy từ request RTR <Mono>#{p.request.display_id ?? p.request.id}</Mono>. Không cần nhập gì – chỉ cần kiểm tra.</>}
        next={{ label: "Đúng – tiếp tục", onClick: next }}
      >
        <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1.5 text-sm">
          <dt className="text-muted">Họ tên</dt><dd className="font-medium">{p.request.full_name}</dd>
          <dt className="text-muted">Mã NV</dt><dd><Mono>{p.request.employee_id}</Mono></dd>
          <dt className="text-muted">Ngày hiệu lực</dt><dd>{formatDate(p.request.effective_date)}</dd>
          {onboard && (<><dt className="text-muted">Band</dt><dd>{p.request.band ?? <span className="text-waiting">Chưa có – nhập ở Kho thiết bị</span>}{p.request.band_source === "override" && <From>nhập tay</From>}</dd></>)}
          {p.request.details.map((d) => (<div key={d.key} className="contents"><dt className="text-muted">{d.label}</dt><dd>{d.value}</dd></div>))}
        </dl>
        <p className="mt-4 text-sm text-muted">Thông tin sai? Sửa trên request ServiceDesk Plus rồi chạy Đồng bộ dữ liệu – Job dùng dữ liệu mới.</p>
      </Wizard>
    );
  }

  if (key === "sdp") {
    const picked = job.params.sdp_user_id ? { id: job.params.sdp_user_id, name: job.params.sdp_user_name ?? job.params.sdp_user_id } : null;
    const match = p.sdp_user;
    const useMatch = !picked && !!match;
    return (
      <Wizard
        label="Chuẩn bị Job"
        steps={steps}
        current={i}
        title={onboard ? "User SDP nhận tài sản" : "User SDP đang giữ tài sản"}
        description="Hệ thống tìm theo Mã NV; bạn chỉ cần xác nhận."
        onBack={back}
        next={{
          label: useMatch ? "Đúng – dùng user này" : "Tiếp tục",
          loading: act.params.isPending,
          disabled: readOnly && useMatch,
          onClick: () => (useMatch ? act.params.mutate({ sdp_user_id: match!.id, sdp_user_name: match!.name }, { onSuccess: next }) : next()),
        }}
      >
        {picked ? (
          <p className="flex flex-wrap items-center gap-2">
            <UserCheck className="size-4 text-done" aria-hidden="true" /><b>{picked.name}</b><From>đã chọn</From>
            {canRun && <Button size="sm" variant="ghost" onClick={() => act.params.mutate({ clear_sdp_user: true })}>Bỏ chọn</Button>}
          </p>
        ) : match ? (
          <p className="flex flex-wrap items-center gap-2">
            <UserCheck className="size-4 text-done" aria-hidden="true" /><b>{match.name}</b>
            <span className="text-sm text-muted">{match.email}</span><From>{`khớp Mã NV ${match.employee_id}`}</From>
          </p>
        ) : (
          <p className="text-sm">
            Chưa thấy user SDP có Mã NV <Mono>{p.request.employee_id}</Mono> (tài khoản có thể chưa được AD đồng bộ sang SDP).
            Không sao: khi chạy, bước “Xác định user SDP” tự chờ và tìm lại.
          </p>
        )}
        {canRun && (
          <details className="mt-4 text-sm">
            <summary className="cursor-pointer text-primary">Không đúng người? Chọn user khác</summary>
            <Field label="Tìm user SDP" className="mt-2 max-w-md">
              <Input type="search" value={userQ} onChange={(e) => setUserQ(e.target.value)} placeholder="Tên, e-mail hoặc Mã NV" />
            </Field>
            {users.data && users.data.length > 0 && (
              <ul className="mt-1 max-w-md divide-y divide-line rounded-sm border border-line">
                {users.data.map((u) => (
                  <li key={u.id} className="flex items-center justify-between gap-2 px-2 py-1.5">
                    <span>{u.name} <span className="text-muted">{u.email} {u.employee_id && `· ${u.employee_id}`}</span></span>
                    <Button size="sm" onClick={() => act.params.mutate({ sdp_user_id: u.id, sdp_user_name: u.name })}>Chọn</Button>
                  </li>
                ))}
              </ul>
            )}
          </details>
        )}
      </Wizard>
    );
  }

  if (key === "devices") {
    const changed = !sameAssets(chosen, current);
    const buys = lines.filter((l) => l.buy > 0);
    return (
      <Wizard
        label="Chuẩn bị Job"
        steps={steps}
        current={i}
        title="Thiết bị cấp phát"
        description={p.request.band ? `Theo tiêu chuẩn band ${p.request.band}, tài sản In Store tận dụng được đã chọn sẵn.` : "Request chưa có band nên chưa đối chiếu được tiêu chuẩn – chọn tài sản từ kho nếu cần."}
        onBack={back}
        aside={canRun && <Button onClick={() => setPickerOpen(true)}><Plus className="size-4" aria-hidden="true" />Thêm từ kho</Button>}
        next={{
          label: chosen.length ? `Dùng ${chosen.length} tài sản này – tiếp tục` : "Tiếp tục không gán tài sản",
          loading: act.params.isPending,
          onClick: () => (changed && canRun ? act.params.mutate({ assets: chosen }, { onSuccess: next }) : next()),
        }}
      >
        {chosen.length === 0 ? (
          <p className="text-sm text-muted">Chưa có tài sản nào – bước “Gán tài sản” sẽ không thay đổi gì.</p>
        ) : (
          <ul className="divide-y divide-line rounded-sm border border-line">
            {chosen.map((a) => (
              <li key={a.id} className="flex items-center gap-2 px-3 py-2">
                <Recycle className="size-4 shrink-0 text-primary" aria-hidden="true" />
                <span className="min-w-0 flex-1">{a.name} <span className="text-sm text-muted">{a.product_type} · <Mono>{a.tag ?? "—"}</Mono></span></span>
                {suggested.some((s) => s.id === a.id) && <From>gợi ý từ kho</From>}
                {canRun && (
                  <Button size="icon" variant="ghost" aria-label={`Bỏ ${a.name}`} onClick={() => setAssets(chosen.filter((x) => x.id !== a.id))}>
                    <Trash2 className="size-4" aria-hidden="true" />
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
        {buys.length > 0 && (
          <section aria-labelledby="buy-title" className="mt-4 rounded-sm border border-waiting/40 bg-waiting-soft px-3 py-2 text-sm">
            <h3 id="buy-title" className="flex items-center gap-1.5 font-medium text-waiting"><ShoppingCart className="size-4" aria-hidden="true" />Kho không còn đủ – cần đề xuất mua</h3>
            <p className="mt-0.5 text-ink">Vẫn bắt đầu Job được. Lập đề xuất ngay để không quên: thiết bị, số lượng và lý do đã điền sẵn.</p>
            <ul className="mt-2 space-y-1.5">
              {buys.map((l) => {
                const done = proposedFor(purchases.data?.items, p.request, l);
                return (
                  <li key={l.label} className="flex flex-wrap items-center gap-2">
                    <span className="min-w-0 flex-1 font-medium text-ink">{l.label} × {l.buy}</span>
                    {done ? (
                      <StatusBadge status="done" label="Đã đề xuất" />
                    ) : can("workload.purchase.create") ? (
                      <Link to={buyLink(p.request, l, `/lifecycle/jobs/${job.id}?view=wizard`)} className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line bg-surface px-3 font-medium text-ink hover:bg-surface-2">
                        <ShoppingCart className="size-4" aria-hidden="true" />Lập đề xuất mua
                      </Link>
                    ) : (
                      <span className="text-muted">Báo Supervisor đề xuất mua</span>
                    )}
                  </li>
                );
              })}
            </ul>
          </section>
        )}
        <AssetPicker open={pickerOpen} onClose={() => setPickerOpen(false)} selected={chosen} onPick={(a) => setAssets([...chosen, a])} />
      </Wizard>
    );
  }

  // Start: what will happen, then go.
  return (
    <Wizard
      label="Chuẩn bị Job"
      steps={steps}
      current={i}
      title="Kiểm tra lần cuối và bắt đầu"
      description="Danh sách việc hệ thống sẽ làm, chưa có gì được ghi lên SDP hay BioStar."
      onBack={back}
      aside={
        canRun && future && (
          <Button loading={act.start.isPending && !act.start.variables} onClick={() => act.start.mutate(null)}>
            <Play className="size-4" aria-hidden="true" />Bắt đầu ngay
          </Button>
        )
      }
      next={
        canRun
          ? future
            ? { label: <><CalendarClock className="size-4" aria-hidden="true" />Chạy vào ngày hiệu lực {formatDate(eff)}</>, loading: act.start.isPending && !!act.start.variables, onClick: () => act.start.mutate(eff!) }
            : { label: <><Play className="size-4" aria-hidden="true" />Bắt đầu ngay</>, loading: act.start.isPending, onClick: () => act.start.mutate(null) }
          : undefined
      }
    >
      {preview.isLoading ? (
        <LoadingRows rows={4} />
      ) : (
        <ol className="space-y-2 text-sm">
          {(preview.data ?? []).map((s, n) => (
            <li key={s.key}>
              <p className="font-medium"><span className="mr-1.5 text-muted tabular">{n + 1}.</span>{s.title}</p>
              {s.error ? <p className="text-failed">{s.error}</p> : s.lines.map((l, k) => <p key={k} className="text-muted">{l}</p>)}
            </li>
          ))}
        </ol>
      )}
    </Wizard>
  );
}
