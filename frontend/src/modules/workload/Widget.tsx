import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CircleCheck } from "lucide-react";
import { api } from "@/core/api/client";
import { LoadingRows, Mono } from "@/core/ui/feedback";
import { formatDateTime } from "@/core/lib/format";
import { useTechnicians, type Ticket } from "./api";

/**
 * Dashboard "next action" for the Helpdesk workload. Operators see their own overdue tasks;
 * managers/supervisors see the team's overdue and unassigned tasks (the API scopes the data).
 */
export function WorkloadNextWidget() {
  const techs = useTechnicians();
  const t = techs.data;
  const scope = t?.view_all ? "" : "me";
  const overdue = useQuery({
    queryKey: ["workload", "tickets", "overdue", scope],
    queryFn: () => api<{ total: number; items: Ticket[] }>(`/api/workload/tickets?${new URLSearchParams({ state: "overdue", technician_id: scope, limit: "5" })}`),
    enabled: !!t,
  });
  if (techs.isLoading || overdue.isLoading) return <LoadingRows rows={3} />;
  if (!t) return null;
  if (!t.view_all && !t.me)
    return <p className="py-3 text-sm">Tài khoản của bạn chưa liên kết với technician trên ServiceDesk Plus nên chưa có task. Liên hệ Manager để liên kết.</p>;

  const mine = t.items.find((x) => x.id === t.me);
  const open = t.view_all ? t.items.reduce((n, x) => n + x.open, 0) + t.unassigned.open : (mine?.open ?? 0);
  const tickets = overdue.data?.items ?? [];
  const total = overdue.data?.total ?? 0;
  const base = t.view_all ? "/workload/tickets" : "/workload/tickets?tech=me";
  const join = base.includes("?") ? "&" : "?";
  return (
    <div className="py-2">
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
        <Link to={base} className="hover:underline"><span className="text-lg font-semibold tabular">{open}</span> task đang chờ{t.view_all ? "" : " của bạn"}</Link>
        <Link to={`${base}${join}state=overdue`} className={total ? "text-failed hover:underline" : "text-muted hover:underline"}>
          <span className="text-lg font-semibold tabular">{total}</span> quá hạn
        </Link>
        {t.view_all && t.unassigned.open > 0 && (
          <Link to="/workload/tickets?tech=none" className="text-waiting hover:underline">
            <span className="text-lg font-semibold tabular">{t.unassigned.open}</span> chưa giao
          </Link>
        )}
      </p>
      {tickets.length ? (
        <>
          <h3 className="mt-3 mb-1 text-sm font-semibold">Quá hạn lâu nhất – xử lý trước</h3>
          <ul className="divide-y divide-line text-sm">
            {tickets.map((x) => (
              <li key={x.id} className="flex flex-wrap items-baseline gap-x-2 py-1.5">
                <Mono className="shrink-0">#{x.display_id ?? x.id}</Mono>
                <span className="min-w-0 flex-1 truncate" title={x.subject}>{x.subject}</span>
                {t.view_all && <span className="shrink-0 text-muted">{x.technician_name ?? "Chưa giao"}</span>}
                <span className="shrink-0 text-failed tabular">hạn {formatDateTime(x.due_by)}</span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="mt-3 flex items-center gap-1.5 text-sm text-done"><CircleCheck className="size-4" aria-hidden="true" />Không có task quá hạn.</p>
      )}
    </div>
  );
}
