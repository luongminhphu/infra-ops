import { Link } from "react-router-dom";
import { RefreshCw, TriangleAlert } from "lucide-react";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { EmptyState, ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { StatusBadge } from "@/core/ui/status";
import { formatDateTime, formatRelative } from "@/core/lib/format";
import { useSyncNow, useSyncStatus } from "../api";
import { RUN_STATUS, SOURCE_LABEL } from "../labels";

export function SyncPage() {
  const { can } = useAuth();
  const status = useSyncStatus(true);
  const syncNow = useSyncNow();
  const task = status.data?.task;
  const runs = status.data?.runs ?? [];
  // A request older than 2 minutes that has not started means no worker is running.
  const stuck =
    task && !task.running && new Date(task.next_run_at).getTime() < Date.now() - 120_000;

  return (
    <>
      <PageHeader
        title="Đồng bộ dữ liệu"
        description="infra-ops đọc ServiceDesk Plus và BioStar bằng tài khoản chỉ đọc theo chu kỳ, lưu lại mỗi lần đọc để đối soát và xem lịch sử."
        actions={
          can("lifecycle.reconciliation.sync") && (
            <Button variant="primary" onClick={() => syncNow.mutate()} loading={syncNow.isPending || task?.running}>
              {!(syncNow.isPending || task?.running) && <RefreshCw className="size-4" aria-hidden="true" />}
              {task?.running ? "Đang đồng bộ…" : "Đồng bộ ngay"}
            </Button>
          )
        }
      />
      {status.isError && <ErrorState error={status.error} onRetry={() => status.refetch()} />}
      {status.isLoading && <LoadingRows rows={4} />}
      {task && (
        <Panel title="Lịch đồng bộ" className="mb-4">
          <dl className="grid gap-x-8 gap-y-2 text-base sm:grid-cols-[max-content_1fr_max-content_1fr]">
            <dt className="text-muted">Chu kỳ</dt>
            <dd>{Math.round(task.interval_seconds / 60)} phút{task.enabled ? "" : " (đang tắt)"}</dd>
            <dt className="text-muted">Lần tới</dt>
            <dd>{task.running ? "Đang chạy" : formatDateTime(task.next_run_at)}</dd>
            <dt className="text-muted">Lần gần nhất</dt>
            <dd>{formatRelative(task.last_finished_at ?? runs.find((r) => r.finished_at)?.finished_at)}</dd>
            <dt className="text-muted">Kết quả</dt>
            <dd>
              {task.last_status === "failure" ? (
                <StatusBadge status="failed" label="Lỗi" />
              ) : task.last_status === "success" ? (
                <StatusBadge status="done" label="Hoàn tất" />
              ) : task.running ? (
                <StatusBadge status="in_progress" />
              ) : (
                "—"
              )}
            </dd>
          </dl>
          {task.last_error && (
            <details className="mt-3 rounded-sm bg-failed-soft p-2 text-sm">
              <summary className="cursor-pointer font-medium text-failed">Lần chạy gần nhất bị lỗi – xem chi tiết</summary>
              <p className="mt-1 break-words text-ink">{task.last_error}</p>
            </details>
          )}
          {stuck && (
            <p className="mt-3 flex items-start gap-2 rounded-sm bg-waiting-soft p-2.5 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-waiting" aria-hidden="true" />
              <span>
                Đã quá giờ chạy nhưng đồng bộ chưa bắt đầu: tiến trình chạy nền của infra-ops có thể đang dừng.{" "}
                {can("core.rbac.manage") ? (
                  <>Kiểm tra dịch vụ <code className="font-mono">worker</code> (lệnh <code className="font-mono">python -m app.worker</code>).</>
                ) : (
                  "Báo quản trị hệ thống để khởi động lại."
                )}
              </span>
            </p>
          )}
        </Panel>
      )}
      <Panel title="Các lần đồng bộ gần đây" bodyClassName="p-0">
        {runs.length === 0 ? (
          <EmptyState icon={RefreshCw} title="Chưa đồng bộ lần nào">
            Cấu hình kết nối ở <Link className="text-primary underline" to="/settings/connections">Kết nối hệ thống</Link> rồi bấm “Đồng bộ ngay”.
          </EmptyState>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-left">
              <caption className="sr-only">Các lần đồng bộ</caption>
              <thead className="bg-surface-2 text-sm text-muted">
                <tr>
                  <th scope="col" className="px-3 py-2 font-semibold">Bắt đầu</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Kích hoạt</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Kết quả</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Request</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">User SDP</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Tài sản</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">User BioStar</th>
                  <th scope="col" className="px-3 py-2 text-right font-semibold">Lệch</th>
                  <th scope="col" className="px-3 py-2 font-semibold">Lỗi</th>
                </tr>
              </thead>
              <tbody>
                {runs.map((r) => (
                  <tr key={r.id} className="border-t border-line align-top" style={{ height: "var(--row-h-comfortable)" }}>
                    <td className="px-3 py-2 tabular whitespace-nowrap">{formatDateTime(r.started_at)}</td>
                    <td className="px-3 py-2 text-sm">{r.trigger === "manual" ? `Thủ công · ${r.requested_by ?? ""}` : "Theo lịch"}</td>
                    <td className="px-3 py-2"><StatusBadge status={RUN_STATUS[r.status].status} label={RUN_STATUS[r.status].label} /></td>
                    <td className="px-3 py-2 text-right tabular">{r.stats.requests ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular">{r.stats.sdp_users ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular">{r.stats.assets ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular">{r.stats.biostar_users ?? "—"}</td>
                    <td className="px-3 py-2 text-right tabular">{r.stats.issues ?? "—"}</td>
                    <td className="max-w-md px-3 py-2 text-sm">
                      {/* One line per failed source; the full message (often long) on demand. */}
                      {Object.entries(r.errors ?? {}).map(([k, v]) => (
                        <details key={k}>
                          <summary className="inline-flex min-h-6 cursor-pointer items-center py-0.5 text-failed"><span className="font-medium">{SOURCE_LABEL[k] ?? k}</span>: không đọc được</summary>
                          <p className="mt-1 break-words text-ink">{v}</p>
                        </details>
                      ))}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Panel>
    </>
  );
}
