import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, Hand, Inbox } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { EmptyState, ErrorState, LoadingRows, Mono, Panel } from "@/core/ui/feedback";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import { formatDate } from "@/core/lib/format";
import { JOB_STATUS, JOB_WORKFLOWS, WORKFLOW_LABEL, jobStatusLabel, type Job } from "./jobs";

/** Open jobs split into the unclaimed queue (oldest effective date first) and the work each person holds. */
export function splitQueue(jobs: Job[]): { waiting: Job[]; byPerson: { name: string; userId: string | null; jobs: Job[] }[] } {
  const byDate = (a: Job, b: Job) => (a.params.effective_date ?? "9").localeCompare(b.params.effective_date ?? "9");
  const waiting = jobs.filter((j) => !j.assignee).sort(byDate);
  const people = new Map<string, { name: string; userId: string | null; jobs: Job[] }>();
  for (const j of jobs.filter((x) => x.assignee)) {
    const key = j.assignee!.user_id ?? j.assignee!.name ?? "?";
    const p = people.get(key) ?? { name: j.assignee!.name ?? "?", userId: j.assignee!.user_id, jobs: [] };
    p.jobs.push(j);
    people.set(key, p);
  }
  const byPerson = [...people.values()].map((p) => ({ ...p, jobs: p.jobs.sort(byDate) })).sort((a, b) => a.jobs.length - b.jobs.length || a.name.localeCompare(b.name));
  return { waiting, byPerson };
}

function stepText(j: Job): string {
  const s = j.steps.find((x) => x.key === j.current_step);
  return s ? `Bước ${s.seq}/${j.progress.total}: ${s.title}` : `Xong ${j.progress.done}/${j.progress.total} bước`;
}

function JobLine({ j, action }: { j: Job; action?: React.ReactNode }) {
  return (
    <li className="flex items-start gap-3 py-2">
      <Link to={`/lifecycle/jobs/${j.id}`} className="min-w-0 flex-1 hover:underline-offset-2">
        <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="font-medium">{WORKFLOW_LABEL[j.workflow_key] ?? j.workflow_title} · {j.subject_label}</span>
          <StatusBadge status={j.stalled_minutes ? "warning" : JOB_STATUS[j.status].status} label={jobStatusLabel(j)} />
        </span>
        <span className="mt-0.5 block text-sm text-muted">
          Hiệu lực {formatDate(j.params.effective_date)} · request <Mono>#{j.params.request_display_id ?? j.external_ref}</Mono> · {stepText(j)}
        </span>
      </Link>
      {action ?? <ChevronRight className="mt-1 size-4 shrink-0 text-muted" aria-hidden="true" />}
    </li>
  );
}

/**
 * Portal section: the team's RTR queue. Nobody is assigned automatically: each technician takes the next request
 * ("Nhận" – SDP is updated with their own API key) and everyone sees who holds what and at which step.
 * Supervisors / managers move work between people from the job page ("Chuyển Job").
 */
export function TeamQueueSection() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["jobs", "team", "open"],
    queryFn: () => api<{ items: Job[] }>(`/api/core/jobs?status=open&limit=500&workflows=${JOB_WORKFLOWS}`),
    refetchInterval: 30_000,
  });
  const claim = useMutation({
    mutationFn: (j: Job) => api(`/api/lifecycle/jobs/${j.id}/claim`, { method: "POST" }),
    onSuccess: (_r, j) => {
      toast.success(`Đã nhận ${WORKFLOW_LABEL[j.workflow_key] ?? "Job"} ${j.subject_label}. SDP được cập nhật Technician là bạn trong ít giây.`);
      void qc.invalidateQueries({ queryKey: ["jobs"] });
    },
    onError: (e) => {
      toast.error(errorMessage(e));
      void qc.invalidateQueries({ queryKey: ["jobs", "team"] });
    },
  });
  const { waiting, byPerson } = splitQueue(q.data?.items ?? []);
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-1.5">
          Hàng đợi request RTR của team
          <HelpTip title="Cách nhận việc">
            <span className="block">Request RTR mới vào hàng đợi “Chờ nhận”. Ai rảnh bấm “Nhận”: infra-ops ghi bạn là Technician của request trên SDP bằng khoá API của bạn (Khoá API của tôi).</span>
            <span className="block">Ai nghỉ: Supervisor hoặc Manager mở Job và bấm “Chuyển Job” để giao cho người khác.</span>
          </HelpTip>
        </span>
      }
      description="Mọi người cùng thấy việc còn tồn, ai đang xử lý và đang ở bước nào."
    >
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading ? (
        <LoadingRows rows={4} />
      ) : (
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
                      <Button size="sm" variant="primary" loading={claim.isPending && claim.variables?.id === j.id} onClick={() => claim.mutate(j)} aria-label={`Nhận ${j.subject_label}`}>
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
              <details key={p.userId ?? p.name} open={p.userId === me?.id} className="border-b border-line py-1 last:border-b-0">
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
      )}
    </Panel>
  );
}
