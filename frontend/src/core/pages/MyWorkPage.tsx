import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronRight, PartyPopper } from "lucide-react";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDate } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { EmptyState, ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { HelpTip } from "@/core/ui/help-tip";
import { STATUS, StatusBadge, type Status } from "@/core/ui/status";
import { modules } from "@/modules";

export interface NextItem {
  key: string;
  kind: string;
  priority: number;
  title: string;
  detail: string;
  due: string | null;
  href: string;
  action: string;
}

export const LEVEL: Record<number, { label: string; status: Status }> = {
  0: { label: "Cần bạn làm ngay", status: "waiting" },
  1: { label: "Quá hạn / gấp", status: "failed" },
  2: { label: "Sắp đến hạn", status: "warning" },
  3: { label: "Cần biết", status: "pending" },
  4: { label: "Gợi ý", status: "skipped" },
};

interface SummaryItem {
  key: string;
  group: "done" | "late" | "soon" | "open";
  label: string;
  count: number;
  href: string;
}

const GROUPS: { key: SummaryItem["group"]; label: string; status: Status }[] = [
  { key: "late", label: "Trễ hạn", status: "failed" },
  { key: "soon", label: "Đến hạn trong 3 ngày", status: "waiting" },
  { key: "open", label: "Đang mở", status: "in_progress" },
  { key: "done", label: "Đã xong 7 ngày qua", status: "done" },
];

/** Counts per group across modules so the person can plan the day (what is late, what is coming, what got done). */
function Summary() {
  const q = useQuery({
    queryKey: ["me", "summary"],
    queryFn: () => api<{ items: SummaryItem[]; failed: string[] }>("/api/core/me/summary"),
    refetchInterval: 60_000,
  });
  if (q.isLoading || q.isError || !q.data?.items.length) return null;
  return (
    <section aria-labelledby="summary-title" className="mb-6">
      <h2 id="summary-title" className="mb-2 flex items-center gap-1 text-sm font-semibold text-muted">
        Tổng quan
        <HelpTip title="Tổng quan">
          <span className="block">Đếm task SDP và Job onboard/offboard đang giao cho bạn.</span>
          <span className="block">Trễ hạn: task quá hạn xử lý, Job đã qua ngày hiệu lực mà chưa xong. Đến hạn: trong 3 ngày tới.</span>
        </HelpTip>
      </h2>
      <ul className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {GROUPS.map((g) => {
          const rows = q.data.items.filter((i) => i.group === g.key);
          const total = rows.reduce((n, i) => n + i.count, 0);
          const def = STATUS[g.status];
          const Icon = def.icon;
          const alert = total > 0 && (g.key === "late" || g.key === "soon");
          return (
            <li key={g.key} className={`rounded-md border bg-surface p-3 shadow-card ${alert ? "border-current " + def.fg : "border-line"}`}>
              <p className={`flex items-center gap-1.5 text-sm font-medium ${def.fg}`}>
                <Icon className="size-4 shrink-0" aria-hidden="true" />
                {g.label}
              </p>
              <p className="mt-1 text-2xl font-semibold text-ink tabular">{total}</p>
              <ul className="mt-1 space-y-0.5 text-sm">
                {rows.map((i) => (
                  <li key={i.key}>
                    {i.count > 0 ? (
                      <Link to={i.href} className="text-ink underline decoration-line underline-offset-2 hover:decoration-current">
                        <span className="tabular">{i.count}</span> {i.label}
                      </Link>
                    ) : (
                      <span className="text-muted"><span className="tabular">0</span> {i.label}</span>
                    )}
                  </li>
                ))}
              </ul>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

export const useNextActions = () =>
  useQuery({
    queryKey: ["me", "next"],
    queryFn: () => api<{ items: NextItem[]; failed: string[] }>("/api/core/me/next"),
    refetchInterval: 60_000,
  });

/**
 * "Việc của tôi" as one queue across modules, most urgent first. The top item is the next thing to do;
 * "Làm việc tiếp theo" opens it (wizards end by coming back here, so the operator walks the queue).
 */
export function MyWorkPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const q = useNextActions();
  const sections = modules
    .flatMap((m) => m.portal ?? [])
    .filter((s) => can(s.permission))
    .sort((a, b) => a.order - b.order);
  const items = q.data?.items ?? [];
  const [first, ...rest] = items;
  const groups = [0, 1, 2, 3, 4].map((p) => ({ p, items: rest.filter((i) => i.priority === p) })).filter((g) => g.items.length);

  return (
    <>
      <PageHeader title="Việc của tôi" description="Việc tiếp theo nằm trên cùng; làm xong, quay lại đây để sang việc kế tiếp." />
      <Summary />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading ? (
        <LoadingRows rows={5} />
      ) : !first ? (
        <Panel className="mb-6">
          <EmptyState icon={PartyPopper} title="Không còn việc nào cần làm">
            Job, task quá hạn và đề xuất cần bổ sung sẽ hiện ở đây khi có.
          </EmptyState>
        </Panel>
      ) : (
        <>
          <section aria-labelledby="next-title" className="mb-6 rounded-md border border-primary/40 bg-surface p-5 shadow-card">
            <p className="mb-2 flex flex-wrap items-center gap-2 text-sm text-muted">
              <StatusBadge {...LEVEL[first.priority]!} />
              Việc tiếp theo · còn {items.length} việc
            </p>
            <h2 id="next-title" className="text-lg font-semibold">{first.title}</h2>
            {first.detail && <p className="mt-0.5 text-muted">{first.detail}</p>}
            {first.due && <p className="mt-0.5 text-sm text-muted">Hạn {formatDate(first.due)}</p>}
            <Button variant="primary" className="mt-4" onClick={() => navigate(first.href)}>
              Làm việc tiếp theo: {first.action}<ArrowRight className="size-4" aria-hidden="true" />
            </Button>
          </section>
          {groups.length > 0 && (
            <div className="mb-6 space-y-4">
              {groups.map((g) => (
                <section key={g.p} aria-label={LEVEL[g.p]!.label}>
                  <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold">
                    <StatusBadge {...LEVEL[g.p]!} /> <span className="text-muted tabular">{g.items.length}</span>
                  </h3>
                  <ul className="divide-y divide-line rounded-md border border-line bg-surface">
                    {g.items.map((i) => (
                      <li key={i.key}>
                        <Link to={i.href} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2">
                          <span className="min-w-0 flex-1">
                            <span className="block font-medium">{i.title}</span>
                            {(i.detail || i.due) && (
                              <span className="block truncate text-sm text-muted">
                                {i.detail}{i.detail && i.due ? " · " : ""}{i.due ? `hạn ${formatDate(i.due)}` : ""}
                              </span>
                            )}
                          </span>
                          <span className="shrink-0 text-sm font-medium text-primary">{i.action}</span>
                          <ChevronRight className="size-4 shrink-0 text-muted" aria-hidden="true" />
                        </Link>
                      </li>
                    ))}
                  </ul>
                </section>
              ))}
            </div>
          )}
        </>
      )}
      {q.data?.failed.length ? <p role="status" className="mb-4 text-sm text-waiting">Chưa tải được việc của: {q.data.failed.join(", ")}.</p> : null}
      {sections.length > 0 && (
        <details className="rounded-md border border-line bg-surface px-4 py-3">
          <summary className="cursor-pointer font-medium">Xem theo nhóm: Job, task và gợi ý mua</summary>
          <div className="mt-4 space-y-6">
            {sections.map(({ key, component: Section }) => <Section key={key} />)}
          </div>
        </details>
      )}
    </>
  );
}
