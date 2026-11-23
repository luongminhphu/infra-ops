import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Switch } from "@/core/ui/field";
import { Flowchart } from "@/core/ui/flowchart";
import { Tabs } from "@/core/ui/tabs";
import { formatDateTime } from "@/core/lib/format";
import { SYSTEM_WRITE_LABEL, type WorkflowInfo } from "../jobs";

type Draft = Record<string, { items: { id: string; text: string }[]; evidence_required: boolean }>;

const newId = () => `n${Math.random().toString(36).slice(2, 10)}`;

function Editor({ wf, onDone }: { wf: WorkflowInfo; onDone: () => void }) {
  const qc = useQueryClient();
  const [draft, setDraft] = useState<Draft>(() => Object.fromEntries(wf.steps.map((s) => [s.key, structuredClone(s.checklist)])));
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const empty = Object.values(draft).some((d) => d.items.some((i) => !i.text.trim()));
  const save = useMutation({
    mutationFn: () => api<WorkflowInfo>(`/api/core/workflows/${wf.key}/checklist`, { method: "PUT", json: { steps: draft, note } }),
    onSuccess: (w) => {
      toast.success(`Đã lưu checklist phiên bản ${w.checklist_version}. Job bắt đầu từ bây giờ dùng phiên bản này.`);
      void qc.invalidateQueries({ queryKey: ["workflows"] });
      onDone();
    },
    onError: (e) => toast.error(e instanceof ApiError && e.code === "no_change" ? "Checklist chưa thay đổi." : errorMessage(e)),
  });
  const update = (key: string, f: (d: Draft[string]) => Draft[string]) => setDraft((d) => ({ ...d, [key]: f(d[key]!) }));
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setTried(true);
        if (!empty) save.mutate();
      }}
      className="space-y-3"
      noValidate
    >
      {wf.steps.map((s, i) => {
        const d = draft[s.key]!;
        return (
          <fieldset key={s.key} className="rounded-md border border-line bg-surface p-3">
            <legend className="px-1 font-semibold">{i + 1}. {s.title}</legend>
            <ul className="space-y-2">
              {d.items.map((item, j) => (
                <li key={item.id} className="flex items-start gap-2">
                  <Field label={`Mục ${j + 1}`} className="flex-1" error="Nhập nội dung hoặc xoá mục." showError={tried && !item.text.trim()}>
                    <Input
                      value={item.text}
                      maxLength={300}
                      onChange={(e) => update(s.key, (x) => ({ ...x, items: x.items.map((it) => (it.id === item.id ? { ...it, text: e.target.value } : it)) }))}
                    />
                  </Field>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="mt-6"
                    aria-label={`Xoá mục ${j + 1} của bước ${s.title}`}
                    onClick={() => update(s.key, (x) => ({ ...x, items: x.items.filter((it) => it.id !== item.id) }))}
                  >
                    <Trash2 className="size-4" aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
            <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
              <Button type="button" size="sm" disabled={d.items.length >= 30} onClick={() => update(s.key, (x) => ({ ...x, items: [...x.items, { id: newId(), text: "" }] }))}>
                <Plus className="size-4" aria-hidden="true" />Thêm mục
              </Button>
              <Switch
                label="Bắt buộc đính kèm bằng chứng"
                checked={d.evidence_required}
                onChange={(v) => update(s.key, (x) => ({ ...x, evidence_required: v }))}
              />
            </div>
          </fieldset>
        );
      })}
      <Field label="Ghi chú thay đổi" hint="Ví dụ: thêm bước gọi xác nhận với HR. Ghi vào lịch sử phiên bản và nhật ký kiểm tra.">
        <Input value={note} maxLength={500} onChange={(e) => setNote(e.target.value)} />
      </Field>
      <div className="flex gap-2">
        <Button type="submit" variant="primary" loading={save.isPending}>Lưu phiên bản mới</Button>
        <Button type="button" onClick={onDone}>Huỷ</Button>
      </div>
    </form>
  );
}

export function FlowsPage() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["workflows", "lifecycle"], queryFn: () => api<WorkflowInfo[]>("/api/core/workflows?module=lifecycle") });
  const [tab, setTab] = useState("lifecycle.onboard");
  const [editing, setEditing] = useState(false);
  const wf = q.data?.find((w) => w.key === tab) ?? q.data?.[0];
  return (
    <>
      <PageHeader
        title="Lưu đồ quy trình"
        description="Các bước Onboard / Offboard, checklist Operator phải kiểm tra sau mỗi bước và các bước cần bằng chứng. Job đang chạy giữ checklist của lúc bắt đầu."
        actions={
          wf && can("core.workflow.checklist.manage") && !editing && (
            <Button onClick={() => setEditing(true)}><Pencil className="size-4" aria-hidden="true" />Sửa checklist</Button>
          )
        }
      />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading && <LoadingRows rows={8} />}
      {q.data && wf && (
        <Tabs
          label="Quy trình"
          value={wf.key}
          onChange={(k) => {
            setTab(k);
            setEditing(false);
          }}
          tabs={q.data.map((w) => ({ key: w.key, label: w.title }))}
        >
          <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_300px]">
            <div>
              {editing ? (
                <Editor key={wf.key} wf={wf} onDone={() => setEditing(false)} />
              ) : (
                <Flowchart
                  label={`Lưu đồ ${wf.title}`}
                  verify={wf.verify_steps}
                  systemLabel={SYSTEM_WRITE_LABEL}
                  steps={wf.steps.map((s) => ({
                    key: s.key,
                    title: s.title,
                    kind: s.kind,
                    description: s.description,
                    writes: s.writes,
                    confirm: s.confirm,
                    checklist: s.checklist.items,
                    evidenceRequired: s.checklist.evidence_required,
                  }))}
                />
              )}
            </div>
            <Panel title="Phiên bản checklist" bodyClassName="p-0">
              <ol className="divide-y divide-line text-sm">
                {wf.history.map((h) => (
                  <li key={h.version} className="px-4 py-2">
                    <p className="font-medium">Phiên bản {h.version}{h.version === wf.checklist_version && " · đang dùng"}</p>
                    {h.note && <p>{h.note}</p>}
                    <p className="text-muted">{h.created_by} · {formatDateTime(h.created_at)}</p>
                  </li>
                ))}
                <li className="px-4 py-2">
                  <p className="font-medium">Phiên bản 0 – mặc định{wf.checklist_version === 0 && " · đang dùng"}</p>
                  <p className="text-muted">Theo thiết kế ban đầu của infra-ops</p>
                </li>
              </ol>
            </Panel>
          </div>
        </Tabs>
      )}
    </>
  );
}
