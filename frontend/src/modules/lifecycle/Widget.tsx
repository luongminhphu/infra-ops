import { Link } from "react-router-dom";
import { GitCompareArrows } from "lucide-react";
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
