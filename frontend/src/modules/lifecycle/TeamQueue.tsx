import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronRight, Hand, Inbox } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { EmptyState, ErrorState, LoadingRows, Mono } from "@/core/ui/feedback";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import { formatDate, isoDateVn } from "@/core/lib/format";
import { JOB_STATUS, JOB_WORKFLOWS, WORKFLOW_LABEL, jobStatusLabel, type JobListItem } from "./jobs";
import { useClaimJob } from "./useClaimJob";

/** Open jobs split into the unclaimed queue (oldest effective date first) and the work each person holds. */
export function splitQueue(jobs: JobListItem[]): { waiting: JobListItem[]; byPerson: { name: string; userId: string | null; jobs: JobListItem[] }[] } {
  const byDate = (a: JobListItem, b: JobListItem) => (a.params.effective_date ?? "9").localeCompare(b.params.effective_date ?? "9");
  // Offboards past their effective date first: access must not outlive the last working day.
  const late = (j: JobListItem) => (j.params.kind === "offboard" && daysLate(j) > 0 ? 0 : 1);
  const waiting = jobs.filter((j) => !j.assignee).sort((a, b) => late(a) - late(b) || byDate(a, b));
  const people = new Map<string, { name: string; userId: string | null; jobs: JobListItem[] }>();
  for (const j of jobs.filter((x) => x.assignee)) {
    const key = j.assignee!.user_id ?? j.assignee!.name ?? "?";
    const p = people.get(key) ?? { name: j.assignee!.name ?? "?", userId: j.assignee!.user_id, jobs: [] };
    p.jobs.push(j);
    people.set(key, p);
  }
  const byPerson = [...people.values()].map((p) => ({ ...p, jobs: p.jobs.sort(byDate) })).sort((a, b) => a.jobs.length - b.jobs.length || a.name.localeCompare(b.name));
  return { waiting, byPerson };
}

/** Whole days past the effective date (Vietnam calendar), 0 when not late. */
export function daysLate(j: Pick<JobListItem, "params">, today: string = isoDateVn()): number {
  const eff = j.params.effective_date;
  if (!eff || eff >= today) return 0;
  return Math.round((Date.parse(today) - Date.parse(eff)) / 86_400_000);
}

function stepText(j: JobListItem): string {
  const s = j.steps.find((x) => x.key === j.current_step);
  return s ? `Bước ${s.seq}/${j.progress.total}: ${s.title}` : `Xong ${j.progress.done}/${j.progress.total} bước`;
}

function JobLine({ j, action }: { j: JobListItem; action?: React.ReactNode }) {
  return (
    <li className="flex items-start gap-3 py-2">
      <Link to={`/lifecycle/jobs/${j.id}`} className="min-w-0 flex-1 hover:underline-offset-2">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium">{WORKFLOW_LABEL[j.workflow_key] ?? j.workflow_title} · {j.subject_label}</span>
          <StatusBadge status={j.stalled_minutes ? "warning" : JOB_STATUS[j.status].status} label={jobStatusLabel(j)} />
          {daysLate(j) > 0 && j.status !== "running" && <StatusBadge status={j.params.kind === "offboard" ? "failed" : "warning"} label={`Trễ ${daysLate(j)} ngày`} />}
        </span>
        <span className="mt-0.5 block text-sm text-muted">
          Hiệu lực {formatDate(j.params.effective_date)} · request <Mono className="whitespace-nowrap">#{j.params.request_display_id ?? j.external_ref}</Mono> · {stepText(j)}
        </span>
      </Link>
      {action ?? <ChevronRight className="mt-1 size-4 shrink-0 text-muted" aria-hidden="true" />}
    </li>
  );
}

/**
 * Dashboard widget (main page): the team's RTR queue. Nothing is assigned automatically: each technician takes the
 * next request ("Nhận" – SDP is updated with their own API key) and it then shows in their "Việc của tôi"; everyone
 * sees who holds what and at which step. Supervisors / managers move work between people ("Chuyển Job").
 */
export function TeamQueueWidget() {
  const { me } = useAuth();
  const navigate = useNavigate();
  const q = useQuery({
    queryKey: ["jobs", "team", "open"],
    queryFn: () => api<{ items: JobListItem[] }>(`/api/core/jobs?lean=1&status=open&limit=500&workflows=${JOB_WORKFLOWS}`),
    refetchInterval: 30_000,
  });
  const claim = useClaimJob(undefined, (id) => {
    const j = q.data?.items.find((x) => x.id === id);
    toast.success(`Đã nhận ${j ? `${WORKFLOW_LABEL[j.workflow_key] ?? "Job"} ${j.subject_label}` : "Job"}. SDP ghi bạn là Technician trong ít giây; Job chuyển sang Việc của tôi.`, {
      action: { label: "Mở Việc của tôi", onClick: () => navigate("/me/work") },
    });
  });
  const { waiting, byPerson } = splitQueue(q.data?.items ?? []);
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.isLoading) return <LoadingRows rows={4} />;
  return (
    <div className="py-2">
      <p className="mb-3 text-sm text-muted">
        Ai rảnh bấm “Nhận” – infra-ops ghi bạn là Technician của request trên SDP bằng khoá API của bạn, Job chuyển sang Việc của tôi.
        <HelpTip title="Ai nghỉ thì sao?" className="ml-1">
          <span className="block">Supervisor hoặc Manager mở Job của người nghỉ và bấm “Chuyển Job” để giao cho người khác.</span>
          <span className="block">Chưa nhập khoá API cá nhân thì việc ghi lên SDP chờ đến khi bạn nhập ở “Khoá API của tôi”.</span>
        </HelpTip>
      </p>
      <div className="grid gap-6 lg:grid-cols-2">
        <section aria-label="Chờ nhận">
          <h3 className="flex items-baseline gap-2 text-base font-semibold">
            Chờ nhận <span className="text-sm font-normal text-muted tabular">{waiting.length}</span>
          </h3>
          {waiting.length ? (
            <ul className="divide-y divide-line">
              {waiting.map((j) => (
                <JobLine
                  key={j.id}
                  j={j}
                  action={
                    <Button size="sm" loading={claim.isPending && claim.variables === j.id} onClick={() => claim.mutate(j.id)} aria-label={`Nhận ${j.subject_label}`}>
                      <Hand className="size-4" aria-hidden="true" />Nhận
                    </Button>
                  }
                />
              ))}
            </ul>
          ) : (
            <EmptyState icon={Inbox} title="Không còn request chờ nhận" className="py-6">Request RTR mới sẽ xuất hiện ở đây sau lần đồng bộ tới.</EmptyState>
          )}
        </section>
        <section aria-label="Đang xử lý">
          <h3 className="flex items-baseline gap-2 text-base font-semibold">
            Đang xử lý <span className="text-sm font-normal text-muted tabular">{byPerson.reduce((n, p) => n + p.jobs.length, 0)}</span>
          </h3>
          {byPerson.length === 0 && <p className="py-2 text-sm text-muted">Chưa ai nhận request nào.</p>}
          {byPerson.map((p) => (
            <details key={p.userId ?? p.name} className="border-b border-line py-1 last:border-b-0">
              <summary className="flex min-h-8 cursor-pointer items-center gap-2 py-1">
                <span className="font-medium">{p.name}{p.userId === me?.id && <span className="ml-1 text-sm font-normal text-muted">(bạn)</span>}</span>
                <span className="text-sm text-muted tabular">{p.jobs.length} việc</span>
              </summary>
              <ul className="divide-y divide-line pl-1">
                {p.jobs.map((j) => <JobLine key={j.id} j={j} />)}
              </ul>
            </details>
          ))}
        </section>
      </div>
    </div>
  );
}
