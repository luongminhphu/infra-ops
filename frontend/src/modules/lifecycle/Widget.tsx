import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/core/api/client";
import { jobStatusLabel, WORKFLOW_LABEL, type Job } from "./jobs";
import { ChevronRight, GitCompareArrows } from "lucide-react";
import { EmptyState } from "@/core/ui/feedback";
import { StatusBadge } from "@/core/ui/status";
import { formatRelative } from "@/core/lib/format";
import type { Severity, SyncRun } from "./api";
import { RUN_STATUS, SEVERITY } from "./labels";

interface Data {
  run: SyncRun | null;
  severity?: Partial<Record<Severity, number>>;
  employees?: number;
  top_rules?: { rule: string; title: string; severity: Severity; count: number }[];
}

/** Dashboard tile: open jobs by state, each linking to the filtered list. */
export function JobsWidget({ data }: { data: unknown }) {
  const rows = (data as { counts: { workflow: string; status: string; count: number }[] }).counts;
  const sum = (statuses: string[]) => rows.filter((r) => statuses.includes(r.status)).reduce((n, r) => n + r.count, 0);
  const tiles = [
    { label: "Đang mở", value: sum(["running", "waiting", "scheduled", "failed"]), to: "open", tone: "" },
    { label: "Chờ người xử lý / chờ ký", value: sum(["waiting"]), to: "waiting", tone: "text-waiting" },
    { label: "Lỗi", value: sum(["failed"]), to: "failed", tone: "text-failed" },
    { label: "Nháp chưa bắt đầu", value: sum(["draft"]), to: "draft", tone: "" },
  ];
  return (
    <div className="py-2">
      <WaitingJobs />
    <ul className="grid grid-cols-2 gap-2 sm:grid-cols-4" aria-label="Job theo trạng thái">
      {tiles.map((t) => (
        <li key={t.to}>
          <Link to={`/lifecycle/jobs?status=${t.to}`} className="block rounded-sm border border-line px-3 py-2 hover:bg-surface-2">
            <span className="block text-sm text-muted">{t.label}</span>
            <span className={`block tabular text-2xl font-semibold ${t.value > 0 ? t.tone : ""}`}>{t.value}</span>
          </Link>
        </li>
      ))}
    </ul>
    </div>
  );
}

/** The next action on onboarding/offboarding: jobs stopped on a step that needs a person. */
function WaitingJobs() {
  const q = useQuery({
    queryKey: ["jobs", "dashboard-waiting"],
    queryFn: () => api<{ items: Job[] }>("/api/core/jobs?status=waiting&limit=6"),
    refetchInterval: 30_000,
  });
  const jobs = (q.data?.items ?? []).filter((j) => j.workflow_key.startsWith("lifecycle."));
  if (!jobs.length) return null;
  return (
    <div className="mb-3">
      <h3 className="mb-1 text-sm font-semibold">Cần xử lý tiếp</h3>
      <ul className="divide-y divide-line border-y border-line text-sm">
        {jobs.map((j) => (
          <li key={j.id}>
            <Link to={`/lifecycle/jobs/${j.id}`} className="flex items-center gap-3 px-1 py-2 hover:bg-surface-2">
              <span className="flex min-w-0 flex-1 flex-wrap items-baseline gap-x-3">
                <span className="font-medium">{WORKFLOW_LABEL[j.workflow_key] ?? j.workflow_title} · {j.subject_label}</span>
                <span className="text-waiting">{jobStatusLabel(j)}</span>
              </span>
              <ChevronRight className="size-4 shrink-0 text-muted" aria-hidden="true" />
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}

/** Dashboard tile: severity counts as text + proportion bars (counts are always shown as numbers). */
export function ReconciliationWidget({ data }: { data: unknown }) {
  const d = data as Data;
  if (!d.run)
    return (
      <EmptyState icon={GitCompareArrows} title="Chưa có dữ liệu đối soát">
        Chạy đồng bộ ở trang <Link className="text-primary underline" to="/lifecycle/sync">Đồng bộ dữ liệu</Link>.
      </EmptyState>
    );
  const max = Math.max(1, ...(d.top_rules ?? []).map((r) => r.count));
  return (
    <div className="grid gap-4 py-2 lg:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]">
      <div>
        <div className="flex flex-wrap items-center gap-2 text-sm text-muted">
          <StatusBadge status={RUN_STATUS[d.run.status].status} label={RUN_STATUS[d.run.status].label} />
          Đồng bộ {formatRelative(d.run.finished_at)} · {d.employees ?? 0} nhân viên
        </div>
        <ul className="mt-3 grid grid-cols-3 gap-2" aria-label="Nhân viên đang lệch theo mức độ">
          {(["high", "medium", "low"] as const).map((s) => (
            <li key={s}>
              <Link to={`/lifecycle/reconciliation?severity=${s}`} className="block rounded-sm border border-line px-3 py-2 hover:bg-surface-2">
                <span className="block text-sm text-muted">{SEVERITY[s].label}</span>
                <span className={`block tabular text-2xl font-semibold ${s === "high" ? "text-failed" : s === "medium" ? "text-mismatch" : "text-waiting"}`}>
                  {d.severity?.[s] ?? 0}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <h3 className="mb-2 text-sm font-semibold">Loại lệch nhiều nhất</h3>
        {(d.top_rules ?? []).length === 0 ? (
          <p className="text-sm text-muted">Không có lệch đang mở.</p>
        ) : (
          <ul className="space-y-1.5">
            {d.top_rules!.map((r) => (
              <li key={r.rule}>
                <Link to={`/lifecycle/reconciliation?rule=${r.rule}`} className="group grid grid-cols-[minmax(0,1fr)_3rem] items-center gap-3 text-sm">
                  <span className="min-w-0">
                    <span className="block truncate group-hover:text-primary group-hover:underline">{r.title}</span>
                    <span className="mt-0.5 block h-1.5 rounded-full bg-surface-2" aria-hidden="true">
                      <span className={`block h-1.5 rounded-full ${r.severity === "high" ? "bg-failed" : r.severity === "medium" ? "bg-mismatch" : "bg-waiting"}`} style={{ width: `${(r.count / max) * 100}%` }} />
                    </span>
                  </span>
                  <span className="text-right font-medium tabular">{r.count}</span>
                </Link>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
