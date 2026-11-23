import { useEffect, useRef, useState, type ClipboardEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileText, Image, Link2, Paperclip, StickyNote, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input, Textarea } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";
import { formatDateTime } from "@/core/lib/format";
import type { Evidence, Job, JobStep } from "../jobs";

const KIND_ICON = { image: Image, file: FileText, link: Link2, note: StickyNote } as const;
const KIND_LABEL = { image: "Ảnh", file: "Tệp", link: "Liên kết", note: "Ghi chú" } as const;
const ACCEPT = "image/png,image/jpeg,application/pdf,.docx";

function EvidenceItem({ job, e, canDelete, onDelete, busy }: { job: Job; e: Evidence; canDelete: boolean; onDelete: () => void; busy: boolean }) {
  const Icon = KIND_ICON[e.kind];
  return (
    <li className="flex items-start gap-2 py-1.5 text-sm">
      <Icon className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
      <div className="min-w-0 flex-1">
        <span className="sr-only">{KIND_LABEL[e.kind]}: </span>
        {e.kind === "image" || e.kind === "file" ? (
          <a className="font-medium text-primary hover:underline" href={`/api/core/jobs/${job.id}/evidence/${e.id}/file`} target="_blank" rel="noopener">
            {e.filename}
          </a>
        ) : e.kind === "link" ? (
          <a className="break-all font-medium text-primary hover:underline" href={e.url ?? "#"} target="_blank" rel="noopener noreferrer">
            {e.url}
          </a>
        ) : null}
        {e.text && <p className="whitespace-pre-wrap">{e.text}</p>}
        <p className="text-xs text-muted">{e.created_by} · {formatDateTime(e.created_at)}</p>
      </div>
      {canDelete && (
        <Button variant="ghost" size="icon" aria-label={`Xoá bằng chứng ${e.filename ?? e.url ?? "ghi chú"}`} loading={busy} onClick={onDelete}>
          <Trash2 className="size-4" aria-hidden="true" />
        </Button>
      )}
    </li>
  );
}

/** Checklist + evidence + confirm/reject for one step of a job that uses step verification. */
export function StepVerification({ job, step, canRun }: { job: Job; step: JobStep; canRun: boolean }) {
  const qc = useQueryClient();
  const [checked, setChecked] = useState<string[]>(() => (step.checklist ?? []).filter((i) => i.checked).map((i) => i.id));
  const [tried, setTried] = useState(false);
  const [link, setLink] = useState("");
  const [note, setNote] = useState("");
  const [rejectOpen, setRejectOpen] = useState(false);
  const [reason, setReason] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);
  useEffect(() => {
    setChecked((step.checklist ?? []).filter((i) => i.checked).map((i) => i.id));
    setTried(false);
  }, [step.key, step.status]); // eslint-disable-line react-hooks/exhaustive-deps

  const base = `/api/core/jobs/${job.id}`;
  const onJob = (text: string) => (j: Job) => {
    qc.setQueryData(["job", j.id], j);
    void qc.invalidateQueries({ queryKey: ["jobs"] });
    toast.success(text);
    announce(text);
  };
  const onErr = (e: unknown) => toast.error(errorMessage(e));
  const upload = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api<Job>(`${base}/steps/${step.key}/evidence/file`, { method: "POST", body: fd });
    },
    onSuccess: onJob("Đã đính kèm bằng chứng."),
    onError: onErr,
  });
  const addOther = useMutation({
    mutationFn: (body: { kind: "link" | "note"; url?: string; text?: string }) =>
      api<Job>(`${base}/steps/${step.key}/evidence`, { method: "POST", json: body }),
    onSuccess: (j, v) => {
      onJob(v.kind === "link" ? "Đã thêm liên kết." : "Đã thêm ghi chú.")(j);
      if (v.kind === "link") setLink("");
      else setNote("");
    },
    onError: onErr,
  });
  const remove = useMutation({
    mutationFn: (id: string) => api<Job>(`${base}/evidence/${id}`, { method: "DELETE" }),
    onSuccess: onJob("Đã xoá bằng chứng."),
    onError: onErr,
  });
  const verify = useMutation({
    mutationFn: (body: { ok: boolean; checked?: string[]; note?: string }) => api<Job>(`${base}/steps/${step.key}/verify`, { method: "POST", json: body }),
    onSuccess: (j, v) => {
      onJob(v.ok ? "Đã xác nhận bước hoàn tất." : "Đã ghi nhận kiểm tra không đạt. Sửa lỗi rồi chạy lại bước.")(j);
      setRejectOpen(false);
    },
    onError: onErr,
  });

  if (!step.checklist) return null;
  const items = step.checklist;
  const active = ["running", "waiting", "failed"].includes(job.status);
  const verifying = step.status === "verify" && active && canRun;
  const canAddEvidence = canRun && active && !["done", "skipped"].includes(step.status);
  const missing = items.filter((i) => !checked.includes(i.id));
  const needEvidence = step.evidence_required && step.evidence.length === 0;

  const onPaste = (e: ClipboardEvent) => {
    if (!canAddEvidence) return;
    const file = Array.from(e.clipboardData.files).find((f) => f.type.startsWith("image/"));
    if (file) {
      e.preventDefault();
      upload.mutate(new File([file], `anh-chup-${Date.now()}.png`, { type: file.type }));
    }
  };

  return (
    <section aria-labelledby={`verify-${step.key}`} className="rounded-sm border border-line p-3" onPaste={onPaste}>
      <h3 id={`verify-${step.key}`} className="mb-2 flex flex-wrap items-center gap-2 font-semibold">
        Kiểm tra và xác nhận
        {step.evidence_required && (
          <span className="inline-flex items-center gap-1 rounded-xs bg-primary-soft px-1.5 py-0.5 text-xs font-medium text-primary">
            <Paperclip className="size-3.5" aria-hidden="true" />Cần bằng chứng
          </span>
        )}
      </h3>
      {step.status === "verify" && (
        <p className="mb-2 text-sm">Bước đã thực hiện xong. Kiểm tra lại trên hệ thống thật, tick từng mục{step.evidence_required ? " và đính kèm bằng chứng" : ""} rồi xác nhận để chuyển sang bước tiếp theo.</p>
      )}

      <fieldset>
        <legend className="sr-only">Checklist {step.title}</legend>
        {items.length ? (
          <ul className="space-y-1">
            {items.map((i) => (
              <li key={i.id}>
                <label className="flex cursor-pointer items-start gap-2 text-sm">
                  <input
                    type="checkbox"
                    className="mt-0.5 size-4 accent-[var(--primary)]"
                    checked={checked.includes(i.id)}
                    disabled={!verifying}
                    onChange={(e) => setChecked((c) => (e.target.checked ? [...c, i.id] : c.filter((x) => x !== i.id)))}
                  />
                  {i.text}
                </label>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">Bước này không có mục checklist.</p>
        )}
      </fieldset>

      <div className="mt-3 border-t border-line pt-2">
        <h4 className="text-sm font-semibold">Bằng chứng ({step.evidence.length})</h4>
        {step.evidence.length > 0 && (
          <ul className="divide-y divide-line">
            {step.evidence.map((e) => (
              <EvidenceItem key={e.id} job={job} e={e} canDelete={canAddEvidence} busy={remove.isPending && remove.variables === e.id} onDelete={() => remove.mutate(e.id)} />
            ))}
          </ul>
        )}
        {canAddEvidence && (
          <div className="mt-2 space-y-2">
            <div className="flex flex-wrap items-center gap-2">
              <input
                ref={fileRef}
                type="file"
                accept={ACCEPT}
                className="sr-only"
                tabIndex={-1}
                aria-hidden="true"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) upload.mutate(f);
                  e.target.value = "";
                }}
              />
              <Button size="sm" loading={upload.isPending} onClick={() => fileRef.current?.click()}>
                <Upload className="size-4" aria-hidden="true" />Tải ảnh / file
              </Button>
              <span className="text-xs text-muted">PNG, JPG, PDF, DOCX · hoặc dán ảnh chụp màn hình (Ctrl+V) vào khung này</span>
            </div>
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (link.trim()) addOther.mutate({ kind: "link", url: link.trim() });
              }}
            >
              <Field label="Liên kết" className="min-w-60 flex-1">
                <Input type="url" inputMode="url" placeholder="https://…" value={link} onChange={(e) => setLink(e.target.value)} />
              </Field>
              <Button type="submit" size="sm" className="mb-0.5" disabled={!link.trim()}>Thêm liên kết</Button>
            </form>
            <form
              className="flex flex-wrap items-end gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                if (note.trim().length >= 3) addOther.mutate({ kind: "note", text: note.trim() });
              }}
            >
              <Field label="Ghi chú" className="min-w-60 flex-1">
                <Textarea rows={2} maxLength={2000} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Đã kiểm tra gì, ở đâu" />
              </Field>
              <Button type="submit" size="sm" className="mb-0.5" disabled={note.trim().length < 3}>Thêm ghi chú</Button>
            </form>
          </div>
        )}
      </div>

      {verifying && (
        <div className="mt-3 border-t border-line pt-3">
          {tried && (missing.length > 0 || needEvidence) && (
            <ul role="alert" className="mb-2 list-disc pl-5 text-sm text-failed">
              {missing.length > 0 && <li>Còn {missing.length} mục chưa kiểm tra.</li>}
              {needEvidence && <li>Bước này cần ít nhất một bằng chứng.</li>}
            </ul>
          )}
          <div className="flex flex-wrap gap-2">
            <Button
              variant="primary"
              loading={verify.isPending && verify.variables?.ok}
              onClick={() => {
                setTried(true);
                if (!missing.length && !needEvidence) verify.mutate({ ok: true, checked });
              }}
            >
              Xác nhận hoàn tất
            </Button>
            <Button variant="ghost" className="text-failed" onClick={() => { setReason(""); setRejectOpen(true); }}>Báo không đạt</Button>
          </div>
        </div>
      )}
      {step.verified_at && (
        <p className="mt-3 text-sm text-done">
          Đã xác nhận bởi {step.verified_by} · {formatDateTime(step.verified_at)}
          {step.verify_note && <span className="block text-ink">{step.verify_note}</span>}
        </p>
      )}

      <Dialog
        open={rejectOpen}
        onClose={() => setRejectOpen(false)}
        title={`Kiểm tra “${step.title}” không đạt`}
        size="sm"
        footer={
          <>
            <Button onClick={() => setRejectOpen(false)}>Huỷ</Button>
            <Button variant="danger" disabled={reason.trim().length < 3} loading={verify.isPending} onClick={() => verify.mutate({ ok: false, note: reason })}>
              Báo không đạt
            </Button>
          </>
        }
      >
        <p className="mb-3 text-sm">Bước chuyển sang lỗi. Sửa trên hệ thống thật (hoặc sửa dữ liệu Job) rồi bấm “Chạy lại bước” – bước đọc trạng thái hiện tại trước khi ghi nên chạy lại an toàn.</p>
        <Field label="Không đạt vì" required>
          <Textarea rows={2} maxLength={1000} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </Dialog>
    </section>
  );
}
