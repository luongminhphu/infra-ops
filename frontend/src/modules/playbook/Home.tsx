import { useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, BookOpen, ChevronRight, Search } from "lucide-react";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { EmptyState, ErrorState, LoadingRows } from "@/core/ui/feedback";
import { WidgetSlot } from "@/core/pages/DashboardPage";
import { FROM_QUEUE, LEVEL, useNextActions } from "@/core/pages/MyWorkPage";
import { StatusBadge } from "@/core/ui/status";
import { fold } from "@/core/lib/format";
import type { GuideSummary } from "./api";

/**
 * The app's entry point: "what do you need to handle?" – one search over the published playbooks, the situations
 * grouped by category, the person's own next actions and the team's RTR queue.
 */
export function PlaybookHome() {
  const { me, can } = useAuth();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const list = useQuery({ queryKey: ["playbook", "guides", ""], queryFn: () => api<{ items: GuideSummary[]; can_edit: boolean }>("/api/playbook/guides") });
  const guides = useMemo(() => (list.data?.items ?? []).filter((g) => g.published_version), [list.data]);
  const hits = q.trim() ? guides.filter((g) => fold(`${g.title} ${g.category} ${g.description}`).includes(fold(q.trim()))) : guides;
  const groups = [...new Set(hits.map((g) => g.category || "Chung"))];
  const first = me?.display_name?.split(" ").at(-1);
  return (
    <div className="mx-auto max-w-5xl">
      <section aria-labelledby="home-title" className="pt-6 pb-8">
        <h1 id="home-title" tabIndex={-1} className="text-2xl font-semibold tracking-tight text-balance focus:outline-none sm:text-3xl">
          {first ? `${first}, bạn cần xử lý việc gì?` : "Bạn cần xử lý việc gì?"}
        </h1>
        <form role="search" className="mt-4" onSubmit={(e) => { e.preventDefault(); if (hits[0]) navigate(`/playbook/${hits[0].id}`); }}>
          <label htmlFor="home-search" className="sr-only">Tìm tình huống</label>
          <div className="relative">
            <Search className="pointer-events-none absolute top-1/2 left-3.5 size-5 -translate-y-1/2 text-muted" aria-hidden="true" />
            <input
              id="home-search"
              type="search"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Ví dụ: nhân viên mới, nghỉ việc, cấp thêm màn hình, chấm công…"
              className="h-12 w-full rounded-md border border-control-border bg-surface pr-4 pl-11 text-base placeholder:text-muted focus-visible:border-primary focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary/30"
            />
          </div>
        </form>
        {list.data?.can_edit && (
          <p className="mt-2 text-sm text-muted">
            Bạn soạn được Playbook – <Link to="/playbook" className="font-medium text-primary hover:underline">mở thư viện để thêm, sửa hoặc xuất bản</Link>.
          </p>
        )}
      </section>

      <div className="grid gap-10 lg:grid-cols-[minmax(0,1fr)_20rem] [&>*]:min-w-0">
        <section aria-label="Tình huống">
          {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
          {list.isLoading ? (
            <LoadingRows rows={5} />
          ) : hits.length === 0 ? (
            <EmptyState icon={BookOpen} title={q ? "Chưa có Playbook cho tình huống này" : "Chưa có Playbook nào được xuất bản"}>
              {q ? "Thử từ khoá khác, hoặc mở Công cụ để làm trực tiếp." : list.data?.can_edit ? "Mở thư viện Playbook để soạn hướng dẫn đầu tiên." : "Manager hoặc Supervisor sẽ xuất bản hướng dẫn tại đây."}
            </EmptyState>
          ) : (
            <div className="space-y-8">
              {groups.map((cat) => (
                <div key={cat}>
                  <h2 className="mb-1 text-sm font-medium text-muted">{cat}</h2>
                  <ul className="divide-y divide-line border-y border-line">
                    {hits.filter((g) => (g.category || "Chung") === cat).map((g) => (
                      <li key={g.id}>
                        <Link to={`/playbook/${g.id}`} className="group flex min-h-14 items-center gap-3 py-3 pr-1">
                          <span className="min-w-0 flex-1">
                            <span className="block font-medium group-hover:text-primary">{g.title}</span>
                            {g.description && <span className="mt-0.5 block truncate text-sm text-muted">{g.description}</span>}
                          </span>
                          <ChevronRight className="size-4 shrink-0 text-muted group-hover:text-primary" aria-hidden="true" />
                        </Link>
                      </li>
                    ))}
                  </ul>
                </div>
              ))}
            </div>
          )}
          <p className="mt-6 text-sm">
            <Link to="/tools" className="inline-flex items-center gap-1 font-medium text-primary hover:underline">Mở trực tiếp một công cụ<ArrowRight className="size-4" aria-hidden="true" /></Link>
          </p>
        </section>
        <aside aria-labelledby="home-next" className="min-w-0">
          <h2 id="home-next" className="mb-1 text-sm font-medium text-muted">Đang chờ bạn</h2>
          <MyNext />
        </aside>
      </div>
      {can("lifecycle.job.execute") && (
        <div className="mt-10">
          <WidgetSlot kind="lifecycle_team_queue" />
        </div>
      )}
    </div>
  );
}

/** The person's next actions, short: what, how urgent, open it. The full list is "Việc của tôi". */
function MyNext() {
  const q = useNextActions();
  const items = (q.data?.items ?? []).filter((i) => !i.passive && i.kind !== "notice");
  if (q.isLoading) return <LoadingRows rows={3} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!items.length) return <p className="border-y border-line py-3 text-sm text-muted">Không có việc nào đang chờ bạn.</p>;
  return (
    <>
      <ul className="divide-y divide-line border-y border-line">
        {items.slice(0, 5).map((i) => (
          <li key={i.key}>
            <Link to={i.href} state={FROM_QUEUE} className="group block py-2.5">
              <span className="block text-sm font-medium group-hover:text-primary">{i.title}</span>
              <span className="mt-1 flex items-center gap-2"><StatusBadge {...LEVEL[i.priority]!} /><span className="text-xs text-primary">{i.action}</span></span>
            </Link>
          </li>
        ))}
      </ul>
      <Link to="/me/work" className="mt-2 inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">Tất cả việc của tôi ({items.length})<ArrowRight className="size-4" aria-hidden="true" /></Link>
    </>
  );
}
