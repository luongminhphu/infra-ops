import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, ChevronRight, Hourglass, Inbox, PartyPopper, TriangleAlert } from "lucide-react";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDate } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { EmptyState, ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { HelpTip } from "@/core/ui/help-tip";
import { STATUS, StatusBadge, type Status } from "@/core/ui/status";
import { FROM_QUEUE, LEVEL, useNextActions } from "@/core/lib/nextActions";
import { useModules } from "@/core/modules";

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
  { key: "open", label: "Đang mở", status: "pending" },
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
        Hôm nay
        <HelpTip title="Hôm nay">
          <span className="block">Đếm task SDP và Job onboard/offboard đang giao cho bạn.</span>
          <span className="block">Trễ hạn: task quá hạn xử lý, Job đã qua ngày hiệu lực và đang chờ bạn (Job đang chờ hệ thống không tính). Đến hạn: trong 3 ngày tới.</span>
        </HelpTip>
      </h2>
      {/* Phones: one line of counts so the next thing to do stays near the top. */}
      <ul className="flex flex-wrap gap-2 sm:hidden">
        {GROUPS.map((g) => {
          const rows = q.data.items.filter((i) => i.group === g.key && i.count > 0);
          const total = rows.reduce((n, i) => n + i.count, 0);
          const def = STATUS[g.status];
          const Icon = def.icon;
          return (
            <li key={g.key}>
              {total ? (
                <Link to={rows[0]!.href} className={`inline-flex h-8 items-center gap-1.5 rounded-full border border-current px-3 text-sm ${def.fg}`}>
                  <Icon className="size-3.5 shrink-0" aria-hidden="true" />
                  {g.label} <span className="font-semibold tabular">{total}</span>
                </Link>
              ) : (
                <span className="inline-flex h-8 items-center gap-1.5 rounded-full border border-line px-3 text-sm text-muted">
                  <Icon className="size-3.5 shrink-0" aria-hidden="true" />
                  {g.label} <span className="font-semibold tabular">{total}</span>
                </span>
              )}
            </li>
          );
        })}
      </ul>
      <ul className="hidden grid-cols-2 gap-3 sm:grid lg:grid-cols-4">
        {GROUPS.map((g) => {
          const rows = q.data.items.filter((i) => i.group === g.key && i.count > 0);
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
              {rows.length === 1 ? (
                // One source: the number itself opens the filtered list (no "15 / 15 task SDP" repeat).
                <Link to={rows[0]!.href} className="mt-1 block text-ink hover:underline">
                  <span className="text-2xl font-semibold tabular">{total}</span> <span className="text-sm">{rows[0]!.label}</span>
                </Link>
              ) : (
                <p className="mt-1 text-2xl font-semibold text-ink tabular">{total}</p>
              )}
              {/* Several sources: each non-zero part opens the list filtered to exactly these items. */}
              {rows.length > 1 && <ul className="mt-1 space-y-0.5 text-sm">
                {rows.map((i) => (
                  <li key={i.key}>
                    <Link to={i.href} className="text-ink underline decoration-line underline-offset-2 hover:decoration-current">
                      <span className="tabular">{i.count}</span> {i.label}
                    </Link>
                  </li>
                ))}
              </ul>}
            </li>
          );
        })}
      </ul>
    </section>
  );
}

/**
 * "Việc của tôi" as one queue across modules, most urgent first. The top item is the next thing to do;
 * "Làm việc tiếp theo" opens it (wizards end by coming back here, so the operator walks the queue).
 */
export function MyWorkPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const q = useNextActions();
  const sections = useModules()
    .flatMap((m) => m.portal ?? [])
    .filter((s) => can(s.permission))
    .sort((a, b) => a.order - b.order);
  const all = q.data?.items ?? [];
  const notices = all.filter((i) => i.kind === "notice");
  // Information notices (priority 4, e.g. work waiting to be taken) – warnings do not make "nothing to do" false.
  const info = notices.filter((n) => n.priority === 4);
  const waiting = all.filter((i) => i.passive && i.kind !== "notice");
  const items = all.filter((i) => !i.passive && i.kind !== "notice");
  const [first, ...rest] = items;
  const groups = [0, 1, 2, 3, 4].map((p) => ({ p, items: rest.filter((i) => i.priority === p) })).filter((g) => g.items.length);
  const [groupsOpen, setGroupsOpen] = useState(false);
  // Alt+N opens the next thing to do (a bare letter key would break WCAG 2.1.4).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.code !== "KeyN" || !e.altKey || e.ctrlKey || e.metaKey || e.shiftKey || !first) return;
      if ((e.target as HTMLElement).closest("input, textarea, select, [contenteditable]")) return;
      e.preventDefault();
      navigate(first.href, { state: FROM_QUEUE });
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [first, navigate]);

  return (
    <>
      <PageHeader title="Việc của tôi" description="Việc tiếp theo nằm trên cùng (phím Alt+N để mở); làm xong, quay lại đây để sang việc kế tiếp." />
      {notices.map((n) => {
        const info = n.priority === 4;
        const Icon = info ? Inbox : TriangleAlert;
        return (
          <div
            key={n.key}
            role={info ? undefined : "alert"}
            className={info ? "mb-4 flex flex-wrap items-start gap-3 rounded-md border border-line bg-surface-2 px-4 py-3" : "mb-4 flex flex-wrap items-start gap-3 rounded-md border border-waiting bg-waiting-soft px-4 py-3"}
          >
            <Icon className={info ? "mt-0.5 size-4 shrink-0 text-muted" : "mt-0.5 size-4 shrink-0 text-waiting"} aria-hidden="true" />
            <div className="min-w-0 flex-1">
              <p className="font-medium">{n.title}</p>
              <p className="text-sm text-pretty">{n.detail}</p>
            </div>
            {!n.passive && <Link to={n.href} state={FROM_QUEUE} className="text-sm font-medium text-primary hover:underline">{n.action}</Link>}
          </div>
        );
      })}
      <Summary />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading ? (
        <LoadingRows rows={5} />
      ) : !first ? (
        <Panel className="mb-6">
          <EmptyState icon={info.length ? Inbox : PartyPopper} title={info.length ? "Bạn chưa nhận việc nào" : "Không còn việc nào cần bạn làm"}>
            {info.length ? "Xem thông báo phía trên." : waiting.length ? `${waiting.length} việc đang chờ hệ thống – xem bên dưới.` : "Job, task quá hạn và đề xuất cần bổ sung sẽ hiện ở đây khi có."}
          </EmptyState>
        </Panel>
      ) : (
        <>
          <section aria-labelledby="next-title" className="mb-6 rounded-md border border-primary/40 bg-surface p-5 shadow-card">
            <p className="mb-2 flex flex-wrap items-center gap-2 text-sm text-muted">
              <StatusBadge {...LEVEL[first.priority]!} />
              Việc tiếp theo{rest.length ? ` · sau việc này còn ${rest.length} việc` : ""}
            </p>
            <h2 id="next-title" className="text-lg font-semibold text-pretty">{first.title}</h2>
            {first.detail && <p className="mt-0.5 text-muted text-pretty">{first.detail}</p>}
            {first.due && <p className="mt-0.5 text-sm text-muted">Hạn {formatDate(first.due)}</p>}
            <Button variant="primary" className="mt-4 h-11 w-full sm:h-9 sm:w-auto" onClick={() => navigate(first.href, { state: FROM_QUEUE })}>
              <span className="truncate">Làm việc tiếp theo: {first.action}</span><ArrowRight className="size-4 shrink-0" aria-hidden="true" />
            </Button>
          </section>
          {groups.length > 0 && (
            <div className="mb-6 space-y-4">
              {groups.map((g) => (
                <section key={g.p} aria-label={LEVEL[g.p]!.label}>
                  <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold">
                    <StatusBadge {...LEVEL[g.p]!} /> <span className="text-muted tabular">{g.items.length} việc</span>
                  </h3>
                  <ul className="divide-y divide-line rounded-md border border-line bg-surface">
                    {g.items.map((i) => (
                      <li key={i.key}>
                        <Link to={i.href} state={FROM_QUEUE} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2">
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
      {waiting.length > 0 && (
        <section aria-label="Đang chờ hệ thống" className="mb-6">
          <h3 className="mb-1 flex items-center gap-2 text-sm font-semibold text-muted">
            <Hourglass className="size-4" aria-hidden="true" />Đang chờ hệ thống – bạn không cần làm gì ({waiting.length})
          </h3>
          <ul className="divide-y divide-line rounded-md border border-line bg-surface">
            {waiting.map((i) => (
              <li key={i.key}>
                <Link to={i.href} state={FROM_QUEUE} className="flex items-center gap-3 px-4 py-2 text-muted hover:bg-surface-2">
                  <span className="min-w-0 flex-1">
                    <span className="block text-ink">{i.title}</span>
                    <span className="block text-sm text-pretty">{i.detail}</span>
                  </span>
                  <ChevronRight className="size-4 shrink-0" aria-hidden="true" />
                </Link>
              </li>
            ))}
          </ul>
        </section>
      )}
      {q.data?.failed.length ? <p role="status" className="mb-4 text-sm text-waiting">Chưa tải được việc của: {q.data.failed.join(", ")}.</p> : null}
      {sections.length > 0 && (
        <details onToggle={(e) => setGroupsOpen(e.currentTarget.open)}>
          <summary className="cursor-pointer py-2 font-medium">Xem theo nhóm: Job, task và gợi ý</summary>
          <div className="mt-4 space-y-6">
            {groupsOpen && sections.map(({ key, component: Section }) => <Section key={key} />)}
          </div>
        </details>
      )}
    </>
  );
}
