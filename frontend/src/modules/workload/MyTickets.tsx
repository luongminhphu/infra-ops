import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShoppingCart } from "lucide-react";
import { api } from "@/core/api/client";
import { HelpTip } from "@/core/ui/help-tip";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { ErrorState, LoadingRows, Mono, Panel } from "@/core/ui/feedback";
import { StatusBadge } from "@/core/ui/status";
import { formatDateTime } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import type { MyWork, Ticket } from "./api";
import { KIND_LABEL, ticketBadge } from "./labels";

type Tab = "in_progress" | "pending" | "on_hold" | "overdue" | "done";
const TABS: { key: Tab; label: string }[] = [
  { key: "in_progress", label: "Đang thực hiện" },
  { key: "pending", label: "Chưa bắt đầu" },
  { key: "on_hold", label: "Tạm dừng" },
  { key: "overdue", label: "Quá hạn" },
  { key: "done", label: "Xong 7 ngày qua" },
];

function TicketList({ items, empty }: { items: Ticket[]; empty: string }) {
  if (!items.length) return <p className="py-3 text-sm text-muted">{empty}</p>;
  return (
    <ul className="divide-y divide-line text-sm">
      {items.map((t) => (
        <li key={t.id} className="flex flex-wrap items-baseline gap-x-3 gap-y-1 py-2">
          <Mono className="shrink-0">#{t.display_id ?? t.id}</Mono>
          <span className="min-w-0 flex-1 basis-48">{t.subject}</span>
          <StatusBadge {...ticketBadge(t)} />
          <span className="text-muted tabular">{t.state === "done" ? `xong ${formatDateTime(t.completed_at)}` : `hạn ${formatDateTime(t.due_by)}`}</span>
        </li>
      ))}
    </ul>
  );
}

/** Portal section: the signed-in operator's own SDP tasks and device purchase suggestions. */
export function MyTicketsSection() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [tab, setTab] = useState<Tab>("in_progress");
  const q = useQuery({ queryKey: ["workload", "my-work"], queryFn: () => api<MyWork>("/api/workload/my-work"), refetchInterval: 60_000 });
  const dismiss = useMutation({
    mutationFn: (id: string) => api(`/api/workload/tickets/${id}/dismiss-purchase`, { method: "POST" }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["workload", "my-work"] }),
  });
  const d = q.data;
  const now = Date.now();
  const list = (k: Tab): Ticket[] =>
    k === "done"
      ? (d?.done ?? [])
      : k === "overdue"
        ? (d?.open ?? []).filter((t) => t.due_by && new Date(t.due_by).getTime() < now)
        : (d?.open ?? []).filter((t) => t.phase === k);
  const count = (k: Tab) => (k === "done" ? d?.counts?.done_7d : d?.counts?.[k]) ?? 0;

  return (
    <Panel
      title="Task Helpdesk / Support"
      description={d?.technician ? `Request trên ServiceDesk Plus giao cho ${d.technician}.` : "Request trên ServiceDesk Plus giao cho bạn."}
      actions={<Link to="/workload/tickets?tech=me" className="text-sm font-medium text-primary hover:underline">Mở danh sách đầy đủ</Link>}
    >
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading && <LoadingRows rows={4} />}
      {d && !d.linked && (
        <p className="text-sm">Tài khoản của bạn chưa liên kết với technician trên ServiceDesk Plus. Liên hệ Manager để liên kết ở Cấu hình công việc.</p>
      )}
      {d?.linked && (
        <div className="grid gap-6 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,1fr)]">
          <div>
            <div role="group" aria-label="Task theo trạng thái" className="mb-2 flex flex-wrap gap-1.5">
              {TABS.map((t) => (
                <Button
                  key={t.key}
                  aria-pressed={tab === t.key}
                  size="sm"
                  variant={tab === t.key ? "primary" : "secondary"}
                  className={cn(tab !== t.key && count(t.key) === 0 && "text-muted", t.key === "overdue" && tab !== t.key && count(t.key) > 0 && "text-failed")}
                  onClick={() => setTab(t.key)}
                >
                  {t.label} <span className="tabular">{count(t.key)}</span>
                </Button>
              ))}
            </div>
            <TicketList items={list(tab)} empty="Không có task nào." />
          </div>
          <section aria-labelledby="po-suggest">
            <h3 id="po-suggest" className="flex items-center gap-2 text-base font-semibold">
              <ShoppingCart className="size-4" aria-hidden="true" />Gợi ý đề xuất mua thiết bị
              <HelpTip title="Gợi ý đề xuất mua">
                <span className="block">Request có từ khoá thay thế / nâng cấp / sửa chữa thiết bị hiện ở đây.</span>
                <span className="block"><b>Lập đề xuất</b>: mở form đề xuất (kèm bằng chứng Function Head). <b>Không cần</b>: ẩn gợi ý của request này khỏi danh sách – không gửi gì lên ServiceDesk Plus.</span>
              </HelpTip>
            </h3>
            <p className="text-sm text-muted">Request của bạn nói về thay thế, nâng cấp hoặc sửa chữa thiết bị.</p>
            {(d.suggestions ?? []).length ? (
              <ul className="mt-2 divide-y divide-line text-sm">
                {d.suggestions!.map((t) => (
                  <li key={t.id} className="py-2">
                    <p><Mono>#{t.display_id ?? t.id}</Mono> {t.subject}</p>
                    <p className="text-muted">{t.device_need ? KIND_LABEL[t.device_need] : ""} · {t.requester_name ?? "—"}</p>
                    {can("workload.purchase.create") && (
                      <div className="mt-1.5 flex gap-2">
                        <Link
                          className="inline-flex h-7 items-center rounded-sm border border-line-strong bg-surface px-2.5 text-sm font-medium hover:bg-surface-2"
                          to={`/workload/purchases/new?${new URLSearchParams({ ticket: t.id, ref: t.display_id ?? t.id, kind: t.device_need ?? "replace", item: t.subject.slice(0, 300), employee: t.requester_name ?? "", reason: `Request #${t.display_id ?? t.id}: ${t.subject}` })}`}
                        >
                          Lập đề xuất
                        </Link>
                        <Button size="sm" loading={dismiss.isPending && dismiss.variables === t.id} onClick={() => dismiss.mutate(t.id)}>Không cần</Button>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="mt-2 text-sm text-muted">Không có gợi ý mới.</p>
            )}
          </section>
        </div>
      )}
    </Panel>
  );
}
