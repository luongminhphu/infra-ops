import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CircleCheck, CircleDashed, ChevronRight } from "lucide-react";
import { api } from "@/core/api/client";
import { ErrorState, LoadingRows, Panel } from "@/core/ui/feedback";
import { StatusBadge } from "@/core/ui/status";
import { formatDate } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import { JOB_STATUS, JOB_WORKFLOWS, WORKFLOW_LABEL, jobStatusLabel, type Job } from "./jobs";

const DAY = 86_400_000;

/** What still has to happen before an upcoming job can run (from the job's own data). */
export function prepItems(j: Job): { text: string; done: boolean }[] {
  const scheduled = j.status === "scheduled";
  if (j.params.kind === "onboard")
    return [
      { text: "Chọn tài sản bàn giao trong kho", done: (j.params.assets?.length ?? 0) > 0 },
      { text: "Tạo tài khoản AD / email trước ngày hiệu lực (thủ công)", done: !!j.params.sdp_user_id },
      { text: "Bắt đầu hoặc lên lịch Job theo ngày hiệu lực", done: scheduled },
    ];
  return [
    { text: "Báo nhân viên mang trả thiết bị vào ngày nghỉ", done: false },
    { text: "Lên lịch Job theo ngày nghỉ việc", done: scheduled },
  ];
}

/** needs-me | running | upcoming, from the job state (pure, tested). */
export function bucket(j: Job): "now" | "running" | "upcoming" {
  if (j.status === "draft" || j.status === "scheduled") return "upcoming";
  const s = j.steps.find((x) => x.key === j.current_step);
  if (j.status === "failed") return "now";
  if (s && (s.status === "verify" || (s.kind === "manual" && s.status === "waiting") || (s.requires_confirm && !s.confirmed_at && s.status === "waiting")))
    return "now";
  return "running";
}

function JobRow({ j, children }: { j: Job; children?: React.ReactNode }) {
  const st = JOB_STATUS[j.status];
  const eff = j.params.effective_date;
  const days = eff ? Math.ceil((new Date(eff).getTime() - Date.now()) / DAY) : null;
  return (
    <li>
      <Link to={`/lifecycle/jobs/${j.id}`} className="flex items-start gap-3 px-1 py-2.5 hover:bg-surface-2">
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <span className="font-medium">{WORKFLOW_LABEL[j.workflow_key] ?? j.workflow_title} · {j.subject_label}</span>
            <StatusBadge status={st.status} label={jobStatusLabel(j)} />
          </span>
          <span className="mt-0.5 block text-sm text-muted">
            Hiệu lực {formatDate(eff)}
            {days !== null && days >= 0 && ` · còn ${days} ngày`}
            {days !== null && days < 0 && ` · đã qua ${-days} ngày`}
          </span>
          {children}
        </span>
        <ChevronRight className="mt-1 size-4 shrink-0 text-muted" aria-hidden="true" />
      </Link>
    </li>
  );
}

function Group({ title, hint, jobs, prep = false }: { title: string; hint: string; jobs: Job[]; prep?: boolean }) {
  return (
    <section aria-label={title}>
      <h3 className="flex items-baseline gap-2 text-base font-semibold">
        {title} <span className="text-sm font-normal text-muted tabular">{jobs.length}</span>
      </h3>
      {jobs.length ? (
        <ul className="divide-y divide-line">
          {jobs.map((j) => (
            <JobRow key={j.id} j={j}>
              {prep && (
                <ul className="mt-1.5 space-y-0.5 text-sm" aria-label="Cần chuẩn bị">
                  {prepItems(j).map((p) => (
                    <li key={p.text} className={cn("flex items-center gap-1.5", p.done && "text-muted line-through")}>
                      {p.done ? <CircleCheck className="size-4 shrink-0 text-done" aria-hidden="true" /> : <CircleDashed className="size-4 shrink-0 text-waiting" aria-hidden="true" />}
                      <span className="sr-only">{p.done ? "Đã xong:" : "Cần làm:"}</span>
                      {p.text}
                    </li>
                  ))}
                </ul>
              )}
            </JobRow>
          ))}
        </ul>
      ) : (
        <p className="py-2 text-sm text-muted">{hint}</p>
      )}
    </section>
  );
}

/** Portal section: onboard/offboard jobs the signed-in operator is responsible for. */
export function MyJobsSection() {
  const open = useQuery({
    queryKey: ["jobs", "mine", "open"],
    queryFn: () => api<{ items: Job[] }>(`/api/core/jobs?assignee=me&status=open&limit=200&workflows=${JOB_WORKFLOWS}`),
    refetchInterval: 30_000,
  });
  const done = useQuery({
    queryKey: ["jobs", "mine", "done"],
    queryFn: () => api<{ items: Job[] }>(`/api/core/jobs?assignee=me&status=completed&limit=10&workflows=${JOB_WORKFLOWS}`),
  });
  const jobs = open.data?.items ?? [];
  const by = (b: ReturnType<typeof bucket>) => jobs.filter((j) => bucket(j) === b);
  const upcoming = by("upcoming").sort((a, b) => (a.params.effective_date ?? "9").localeCompare(b.params.effective_date ?? "9"));
  return (
    <Panel title="Onboard / Offboard" description="Job đang giao cho bạn phụ trách.">
      {open.isError && <ErrorState error={open.error} onRetry={() => open.refetch()} />}
      {open.isLoading ? (
        <LoadingRows rows={4} />
      ) : (
        <div className="grid gap-6 lg:grid-cols-2">
          <Group title="Cần bạn xử lý ngay" hint="Không có bước nào đang chờ bạn." jobs={by("now")} />
          <Group title="Đang chạy" hint="Không có Job nào đang chạy." jobs={by("running")} />
          <Group title="Sắp tới – cần chuẩn bị" hint="Không có Job sắp tới." jobs={upcoming} prep />
          <Group title="Đã xong gần đây" hint="Chưa có Job hoàn tất." jobs={done.data?.items ?? []} />
        </div>
      )}
    </Panel>
  );
}
