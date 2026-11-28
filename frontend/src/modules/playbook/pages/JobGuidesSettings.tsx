import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Save } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Select } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import type { GuideSummary } from "../api";

interface JobGuideSettings {
  workflow_guides: Record<string, string>;
  workflows: { key: string; title: string }[];
}
const KEY = ["playbook", "settings", "job-guides"] as const;

/** Which published Playbook each workflow's Jobs show ("Tự chọn" = the only one with a Job step of that workflow). */
export function JobGuidesSettings() {
  const qc = useQueryClient();
  const settings = useQuery({ queryKey: KEY, queryFn: () => api<JobGuideSettings>("/api/playbook/settings/job-guides") });
  const guides = useQuery({
    queryKey: ["playbook", "guides", ""],
    queryFn: () => api<{ items: GuideSummary[] }>("/api/playbook/guides?q="),
  });
  const [draft, setDraft] = useState<Record<string, string> | null>(null);
  const save = useMutation({
    mutationFn: (m: Record<string, string>) => api<JobGuideSettings>("/api/playbook/settings/job-guides", { method: "PUT", json: { workflow_guides: m } }),
    onSuccess: (d) => {
      qc.setQueryData(KEY, (old: JobGuideSettings | undefined) => (old ? { ...old, workflow_guides: d.workflow_guides } : old));
      setDraft(null);
      toast.success("Đã lưu cách gán Playbook.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const published = (guides.data?.items ?? []).filter((g) => g.published_version !== null);
  const value = draft ?? settings.data?.workflow_guides ?? {};
  // Drop assignments whose Playbook is gone / unpublished or whose workflow no longer exists, so a stale id never blocks saving.
  const clean = (m: Record<string, string>) =>
    Object.fromEntries(Object.entries(m).filter(([k, v]) => v && published.some((g) => g.id === v) && settings.data?.workflows.some((w) => w.key === k)));
  return (
    <>
      <Link to="/playbook" className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
        <ChevronLeft className="size-4" aria-hidden="true" />Infra Playbook
      </Link>
      <PageHeader title="Gán Playbook cho quy trình" description="Job của quy trình nào hiện Playbook nào ở trang Job." />
      {(settings.isError || guides.isError) && <ErrorState error={settings.error ?? guides.error} onRetry={() => { void settings.refetch(); void guides.refetch(); }} />}
      {(settings.isLoading || guides.isLoading) && <LoadingRows rows={4} />}
      {settings.data && guides.data && (
        <Panel
          title={<span className="flex items-center gap-1.5">Quy trình<HelpTip title="Tự chọn"><span className="block">“Tự chọn”: Job dùng Playbook đã xuất bản duy nhất có bước Job của quy trình này. Nếu có nhiều Playbook như vậy, hãy gán một Playbook ở đây.</span></HelpTip></span>}
          actions={<Button variant="primary" disabled={draft === null} loading={save.isPending} onClick={() => save.mutate(clean(value))}><Save className="size-4" aria-hidden="true" />Lưu</Button>}
        >
          <div className="grid gap-3 sm:grid-cols-2">
            {settings.data.workflows.map((w) => (
              <Field key={w.key} label={w.title}>
                <Select value={value[w.key] ?? ""} onChange={(e) => setDraft({ ...value, [w.key]: e.target.value })}>
                  <option value="">Tự chọn</option>
                  {value[w.key] && !published.some((g) => g.id === value[w.key]) && <option value={value[w.key]}>(Playbook đã xoá)</option>}
                  {published.map((g) => <option key={g.id} value={g.id}>{g.title} (phiên bản {g.published_version})</option>)}
                </Select>
              </Field>
            ))}
          </div>
        </Panel>
      )}
    </>
  );
}
