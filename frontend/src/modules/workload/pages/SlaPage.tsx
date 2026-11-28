import { Link, useSearchParams } from "react-router-dom";
import { Database, Gauge, RefreshCw, TriangleAlert } from "lucide-react";
import { useAuth } from "@/core/auth/AuthProvider";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Button } from "@/core/ui/button";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge, type Status } from "@/core/ui/status";
import { formatDateTime } from "@/core/lib/format";
import { TicketActions } from "../TicketActions";
import { useSla, useSyncNow, type Sla, type SlaBreakdownRow, type SlaGroup } from "../api";

const CARDS: { key: SlaGroup; label: string; status: Status; href?: string }[] = [
  { key: "overdue", label: "Trễ hạn", status: "failed", href: "state=overdue" },
  { key: "due_soon", label: "Sắp đến hạn", status: "warning" },
  { key: "on_track", label: "Đúng hạn", status: "done" },
  { key: "on_hold", label: "Tạm dừng", status: "skipped", href: "state=on_hold" },
  { key: "no_due", label: "Không có hạn", status: "pending" },
];

function Breakdown({ title, rows, param }: { title: string; rows: SlaBreakdownRow[]; param?: "tech" }) {
  return (
    <section aria-label={title} className="rounded-md border border-line bg-surface">
      <h2 className="border-b border-line px-3 py-2 text-sm font-semibold">{title}</h2>
      <table className="w-full text-sm">
        <caption className="sr-only">{title}</caption>
        <thead>
          <tr className="text-left text-muted">
            <th scope="col" className="px-3 py-1.5 font-medium">Tên</th>
            <th scope="col" className="px-3 py-1.5 text-right font-medium">Trễ hạn</th>
            <th scope="col" className="px-3 py-1.5 text-right font-medium">Sắp đến hạn</th>
            <th scope="col" className="px-3 py-1.5 text-right font-medium">Đang mở</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.key} className="border-t border-line">
              <th scope="row" className="px-3 py-1.5 text-left font-normal">
                {param && r.key ? <Link className="text-primary hover:underline" to={`/workload/tickets?${param}=${encodeURIComponent(r.key)}`}>{r.label}</Link> : r.label}
              </th>
              <td className={`tabular px-3 py-1.5 text-right ${r.overdue ? "font-semibold text-failed" : ""}`}>{r.overdue}</td>
              <td className="tabular px-3 py-1.5 text-right">{r.due_soon}</td>
              <td className="tabular px-3 py-1.5 text-right">{r.open}</td>
            </tr>
          ))}
          {!rows.length && (
            <tr><td colSpan={4} className="px-3 py-3 text-muted">Chưa có task đang mở.</td></tr>
          )}
        </tbody>
      </table>
    </section>
  );
}

function Aging({ aging }: { aging: Sla["aging"] }) {
  const max = Math.max(1, ...aging.map((a) => a.count));
  return (
    <section aria-label="Tuổi ticket đang mở" className="mb-4 rounded-md border border-line bg-surface p-3">
      <h2 className="mb-2 text-sm font-semibold">Tuổi ticket đang mở</h2>
      <ul className="space-y-1.5 text-sm">
        {aging.map((a) => (
          <li key={a.bucket} className="flex items-center gap-3">
            <span className="w-14 shrink-0 text-muted">{a.bucket.replace("d", " ngày")}</span>
            <span className="h-3 flex-1 rounded-sm bg-surface-2">
              <span className="block h-3 rounded-sm bg-primary" style={{ width: `${(a.count / max) * 100}%` }} />
            </span>
            <span className="tabular w-10 shrink-0 text-right font-medium">{a.count}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export function SlaPage() {
  const { can } = useAuth();
  const [params] = useSearchParams();
  const filters = { tech: params.get("tech") ?? "", group: params.get("group") ?? "", category: params.get("category") ?? "" };
  const sla = useSla(filters);
  const syncNow = useSyncNow();
  const d = sla.data;
  const openCount = d ? Object.values(d.groups).reduce((n, x) => n + x, 0) : 0;
  return (
    <>
      <PageHeader
        title="SLA & tuổi ticket"
        description={
          <>
            {d?.last_success_at ? `Đồng bộ lần cuối: ${formatDateTime(d.last_success_at)} (giờ Việt Nam), từ ServiceDesk Plus.` : "Task đang mở theo hạn xử lý và tuổi."}{" "}
            <HelpTip title="Cách tính">
              <span className="block">Chỉ tính task đang mở. Task đang tạm dừng luôn nằm ở nhóm Tạm dừng, dù hạn đã qua.</span>
              <span className="block">Trễ hạn: hạn xử lý đã qua. Sắp đến hạn: hạn còn dưới {d?.due_soon_hours ?? 24} giờ. Đúng hạn: còn lại. Không có hạn: SDP chưa đặt hạn xử lý.</span>
              <span className="block">Tuổi ticket tính từ ngày tạo. Operator chỉ thấy task của mình.</span>
            </HelpTip>
          </>
        }
      />
      {sla.isError && <ErrorState error={sla.error} onRetry={() => sla.refetch()} />}
      {sla.isLoading && <div className="rounded-md border border-line bg-surface"><LoadingRows rows={6} /></div>}
      {d && !d.last_success_at && (
        <EmptyState
          icon={Database}
          title="Chưa có dữ liệu từ ServiceDesk Plus"
        >
          {can("workload.settings.manage") ? (
            <Link className="text-primary underline" to="/workload/settings">Đồng bộ dữ liệu</Link>
          ) : can("workload.sync.run") ? (
            <Button onClick={() => syncNow.mutate()} loading={syncNow.isPending}>
              <RefreshCw className="size-4" aria-hidden="true" />
              Đồng bộ ngay
            </Button>
          ) : (
            "Nhờ Quản trị kết nối ServiceDesk Plus"
          )}
        </EmptyState>
      )}
      {d?.last_success_at && (
        <>
          {d.last_run_failed && (
            <p role="status" className="mb-4 flex items-center gap-1.5 rounded-md border border-line bg-surface px-3 py-2 text-sm text-waiting">
              <TriangleAlert className="size-4 shrink-0" aria-hidden="true" />
              Lần đồng bộ gần nhất lỗi – số liệu theo lần thành công lúc {formatDateTime(d.last_success_at)}.
            </p>
          )}
          <ul aria-label="Tóm tắt SLA" className="mb-4 grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(170px,1fr))]">
            {CARDS.map((c) => {
              const body = (
                <>
                  <StatusBadge status={c.status} label={c.label} />
                  <span className="mt-1 block text-2xl font-semibold tabular">{d.groups[c.key]}</span>
                </>
              );
              return (
                <li key={c.key}>
                  {c.href ? (
                    <Link
                      to={`/workload/tickets?${c.href}${filters.tech ? `&tech=${encodeURIComponent(filters.tech)}` : ""}`}
                      className="block rounded-md border border-line bg-surface px-3 py-2 hover:bg-surface-2"
                    >
                      {body}
                    </Link>
                  ) : (
                    <div className="rounded-md border border-line bg-surface px-3 py-2">{body}</div>
                  )}
                </li>
              );
            })}
          </ul>
          <Aging aging={d.aging} />
          {openCount ? (
            <div className="mb-4 grid gap-3 lg:grid-cols-2">
              {can("workload.tickets.view_all") && d.by.technician && <Breakdown title="Theo người xử lý" rows={d.by.technician} param="tech" />}
              <Breakdown title="Theo nhóm" rows={d.by.group} />
              <Breakdown title="Theo danh mục" rows={d.by.category} />
              <Breakdown title="Theo mức ưu tiên" rows={d.by.priority} />
            </div>
          ) : (
            <p className="mb-4 text-sm text-muted">Không có task đang mở</p>
          )}
          <section aria-label="Task cần xử lý gấp" className="rounded-md border border-line bg-surface">
            <h2 className="border-b border-line px-3 py-2 text-sm font-semibold">Task cần xử lý gấp (tối đa 50, hạn gần nhất trước)</h2>
            {d.items.length ? (
              <ul className="divide-y divide-line text-sm">
                {d.items.map((t) => (
                  <li key={t.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 px-3 py-2">
                    <Link className="text-primary hover:underline" to={`/workload/tickets?state=all&q=${encodeURIComponent(t.display_id ?? t.id)}`}>
                      <Mono>#{t.display_id ?? t.id}</Mono>
                    </Link>
                    <span className="min-w-0 flex-1 truncate" title={t.subject}>{t.subject}</span>
                    <span className="text-muted">{t.technician_name ?? "Chưa giao"}</span>
                    <span className="tabular whitespace-nowrap">{formatDateTime(t.due_by)}</span>
                    <StatusBadge status={t.overdue ? "failed" : "warning"} label={t.overdue ? "Trễ hạn" : "Sắp đến hạn"} />
                    <TicketActions ticket={t} />
                  </li>
                ))}
              </ul>
            ) : (
              <EmptyState icon={Gauge} title="Không có task trễ hạn hay sắp đến hạn" />
            )}
          </section>
        </>
      )}
    </>
  );
}
