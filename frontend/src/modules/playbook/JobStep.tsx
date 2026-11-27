import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ArrowUpRight, ListChecks, Paperclip, Workflow } from "lucide-react";
import { api } from "@/core/api/client";
import { WORKFLOWS_KEY, type JobRef, type WorkflowLite } from "./api";

export const useWorkflows = () =>
  useQuery({ queryKey: WORKFLOWS_KEY, queryFn: () => api<WorkflowLite[]>("/api/core/workflows"), staleTime: 60_000 });

/** The workflow and step a reference points to, or undefined (removed, module off, or not visible to this person). */
export function findJobStep(workflows: WorkflowLite[] | undefined, ref: JobRef | null | undefined) {
  const wf = workflows?.find((w) => w.key === ref?.workflow);
  const step = wf?.steps.find((s) => s.key === ref?.step);
  return wf && step ? { wf, step } : undefined;
}

/**
 * "Bước của Job" as a reader sees it: the Job step and its current checklist, read live from the workflow – the
 * Playbook never copies them, so editing the checklist in Lưu đồ quy trình updates the guide too.
 */
export function JobStepInfo({ job }: { job: JobRef }) {
  const q = useWorkflows();
  if (q.isLoading) return null;
  const hit = findJobStep(q.data, job);
  if (!hit)
    return (
      <p className="mb-3 rounded-sm bg-waiting-soft px-3 py-2 text-sm text-waiting">
        Bước Job này không còn trong quy trình (hoặc bạn không có quyền xem quy trình này).
      </p>
    );
  const { wf, step } = hit;
  return (
    <section aria-label="Bước của Job" className="mb-3 rounded-md border border-line bg-surface-2 px-3 py-2 text-sm">
      <p className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Workflow className="size-4 text-primary" aria-hidden="true" />
        <span>
          Bước app chạy trong <b className="font-medium">{wf.title}</b>: <b className="font-medium">{step.title}</b>
        </span>
        <span className="rounded-xs bg-surface px-1.5 py-0.5 text-xs">{step.kind === "manual" ? "Thủ công" : "Tự động"}</span>
      </p>
      {step.description && <p className="mt-1 text-muted text-pretty">{step.description}</p>}
      {step.checklist.items.length > 0 && (
        <div className="mt-2">
          <p className="flex items-center gap-1.5 font-medium">
            <ListChecks className="size-4 text-muted" aria-hidden="true" />Checklist kiểm tra trong Job
            {step.checklist.evidence_required && (
              <span className="inline-flex items-center gap-1 rounded-xs bg-primary-soft px-1.5 py-0.5 text-xs font-normal text-primary">
                <Paperclip className="size-3" aria-hidden="true" />Cần bằng chứng
              </span>
            )}
          </p>
          <ul className="mt-1 list-disc pl-6">
            {step.checklist.items.map((i) => <li key={i.id}>{i.text}</li>)}
          </ul>
        </div>
      )}
      <Link to={`/lifecycle/jobs?workflow=${encodeURIComponent(wf.key)}`} className="mt-2 inline-flex items-center gap-1 font-medium text-primary hover:underline">
        Mở Job {wf.title.toLowerCase()}<ArrowUpRight className="size-3.5" aria-hidden="true" />
      </Link>
    </section>
  );
}
