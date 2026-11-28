import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, Download, FileCheck2, FileUp, Paperclip, Printer, Save, Search, Stamp, XCircle } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError, errorDetails, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button, buttonVariants } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { ErrorState, LoadingRows, Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Textarea } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";
import { StatusBadge } from "@/core/ui/status";
import { formatDateTime } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import { DOC_STATUS, KIND_SHORT_TITLE, SHORT_OF, useDocMeta, type Doc, type DocData, type ShortKind } from "../docs";
import { EmpLink } from "./EmployeePanel";
import { DocForm, emptyData, formErrors } from "../DocForm";

function Back() {
  return (
    <Link to="/lifecycle/documents" className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
      <ChevronLeft className="size-4" aria-hidden="true" />
      Biên bản
    </Link>
  );
}

/** Step shown for a new record: pick the employee and prefill from synced SDP data. */
function StartNew({ kind, onReady }: { kind: ShortKind; onReady: (d: DocData, info?: string) => void }) {
  const meta = useDocMeta();
  const [params] = useSearchParams();
  const [emp, setEmp] = useState(params.get("emp") ?? "");
  const [error, setError] = useState("");
  const prefill = useMutation({
    mutationFn: (id: string) => api<DocData & { request_display_id: string | null }>(`/api/lifecycle/documents/prefill?kind=${kind}&employee_id=${encodeURIComponent(id)}`),
    onSuccess: (d) => {
      const base = emptyData(kind, meta.data!);
      onReady({ ...base, ...d, checklist: base.checklist, software: base.software, software_other: base.software_other },
        d.assets.length ? `Đã lấy ${d.assets.length} thiết bị đang gán trên SDP${d.request_display_id ? ` và thông tin từ request #${d.request_display_id}` : ""}. Kiểm tra lại trước khi lưu.` : "Không thấy thiết bị nào đang gán trên SDP. Thêm thiết bị bằng tay.");
    },
    onError: (e) => {
      setError(errorMessage(e));
      announce(errorMessage(e));
    },
  });
  const started = useRef(false);
  useEffect(() => {
    if (!started.current && meta.data && params.get("emp")) {
      started.current = true;
      prefill.mutate(params.get("emp")!);
    }
  }, [meta.data, params, prefill]);
  if (!meta.data) return <LoadingRows rows={3} />;
  return (
    <Panel title="Chọn nhân viên" description="infra-ops điền sẵn thông tin từ request RTR và danh sách thiết bị đang gán trên SDP (lần đồng bộ gần nhất).">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          setError("");
          if (emp.trim()) prefill.mutate(emp.trim());
        }}
      >
        <Field label="Mã nhân viên" className="w-56">
          <Input value={emp} onChange={(e) => setEmp(e.target.value)} className="font-mono" autoFocus placeholder="Ví dụ: 003551 hoặc 3551" />
        </Field>
        <Button type="submit" variant="primary" loading={prefill.isPending} disabled={!emp.trim()}>
          <Search className="size-4" aria-hidden="true" />
          Lấy thông tin
        </Button>
        <Button variant="link" onClick={() => onReady(emptyData(kind, meta.data!, { employee: { id: emp.trim(), full_name: "", department: "", job_title: "", work_location: "" } }))}>
          Nhập tay toàn bộ
        </Button>
      </form>
      {error && <p role="alert" className="mt-3 rounded-sm bg-failed-soft px-2.5 py-2 text-sm text-failed">{error}</p>}
    </Panel>
  );
}

export function DocumentPage() {
  const { id } = useParams();
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { can } = useAuth();
  const meta = useDocMeta();
  const isNew = !id;
  const doc = useQuery({ queryKey: ["document", id], queryFn: () => api<Doc>(`/api/core/documents/${id}`), enabled: !isNew });
  const kind: ShortKind = isNew ? ((params.get("kind") as ShortKind) === "return" ? "return" : "handover") : SHORT_OF[doc.data?.kind ?? ""] ?? "handover";
  const [data, setData] = useState<DocData | null>(null);
  const [dirty, setDirty] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [info, setInfo] = useState("");
  const [serverError, setServerError] = useState("");
  const [finalizeOpen, setFinalizeOpen] = useState(false);
  const [voidOpen, setVoidOpen] = useState(false);
  const [voidReason, setVoidReason] = useState("");
  const [attachReq, setAttachReq] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (doc.data) {
      setData(doc.data.data);
      setDirty(false);
      setAttachReq(doc.data.external_ref ?? "");
    }
  }, [doc.data]);

  const errors = data ? formErrors(data) : {};
  const refresh = async (d: Doc) => {
    qc.setQueryData(["document", d.id], d);
    await qc.invalidateQueries({ queryKey: ["documents"] });
  };

  const save = useMutation({
    mutationFn: async () => {
      if (isNew) return api<Doc>("/api/core/documents", { method: "POST", json: { kind: `lifecycle.${kind}`, data } });
      return api<Doc>(`/api/core/documents/${id}`, { method: "PUT", json: { data, version: doc.data!.version } });
    },
    onSuccess: async (d) => {
      setServerError("");
      await refresh(d);
      setDirty(false);
      toast.success("Đã lưu bản nháp.");
      if (isNew) navigate(`/lifecycle/documents/${d.id}`, { replace: true });
    },
    onError: (e) => {
      setServerError(errorDetails(e));
      announce(errorDetails(e));
    },
  });

  const finalize = useMutation({
    mutationFn: () => api<Doc>(`/api/core/documents/${id}/finalize`, { method: "POST" }),
    onSuccess: async (d) => {
      setFinalizeOpen(false);
      await refresh(d);
      toast.success(`Đã chốt biên bản ${d.number}. Mở PDF để in cho các bên ký.`);
    },
    onError: (e) => toast.error(errorDetails(e)),
  });

  const uploadSigned = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api<Doc>(`/api/core/documents/${id}/signed`, { method: "POST", body: fd });
    },
    onSuccess: async (d) => {
      await refresh(d);
      toast.success("Đã tải lên bản đã ký.");
    },
  });

  const attach = useMutation({
    mutationFn: () => api<Doc>(`/api/lifecycle/documents/${id}/attach-sdp`, { method: "POST", json: { request_id: attachReq || null } }),
    onSuccess: async () => {
      // attach-sdp response carries no per-user flags: refetch instead of caching it.
      await qc.invalidateQueries({ queryKey: ["document", id] });
      await qc.invalidateQueries({ queryKey: ["documents"] });
      toast.success(`Đã đính kèm bản ký vào request #${attachReq}.`);
    },
    onError: async (e) => {
      const hint = e instanceof ApiError && e.details && typeof e.details === "object" ? (e.details as { hint?: string }).hint : undefined;
      toast.error(`${errorMessage(e)}${hint ? ` ${hint}` : ""}`);
      await qc.invalidateQueries({ queryKey: ["document", id] });
    },
  });

  const voidDoc = useMutation({
    mutationFn: () => api<Doc>(`/api/core/documents/${id}/void`, { method: "POST", json: { reason: voidReason } }),
    onSuccess: async (d) => {
      setVoidOpen(false);
      await refresh(d);
      toast.success(d.number ? `Đã huỷ biên bản ${d.number}.` : "Đã huỷ bản nháp.");
    },
  });

  const submitSave = () => {
    setSubmitted(true);
    if (Object.keys(errors).length) {
      announce("Vui lòng sửa các trường được đánh dấu.");
      return;
    }
    save.mutate();
  };

  if (meta.isLoading || (!isNew && doc.isLoading)) return <><Back /><LoadingRows rows={8} /></>;
  if (doc.isError) return <><Back /><ErrorState error={doc.error} onRetry={() => doc.refetch()} /></>;
  const d = doc.data;
  const status = d?.status ?? "draft";
  const editable = status === "draft" && (isNew ? can("lifecycle.documents.create") : !!d?.can_edit);
  const title = d?.number ? `Biên bản ${KIND_SHORT_TITLE[kind].toLowerCase()} ${d.number}` : `Biên bản ${KIND_SHORT_TITLE[kind].toLowerCase()} – bản nháp`;
  const fileUrl = (which: string) => `/api/core/documents/${id}/file/${which}`;
  const linkBtn = (variant: "primary" | "secondary") => cn(buttonVariants({ variant }));

  return (
    <>
      <nav aria-label="Đường dẫn"><Back /></nav>
      <PageHeader
        title={isNew ? `Lập biên bản ${KIND_SHORT_TITLE[kind].toLowerCase()}` : title}
        description={
          d ? (
            <span className="inline-flex flex-wrap items-center gap-2">
              <StatusBadge status={DOC_STATUS[status].status} label={DOC_STATUS[status].label} />
              {d.subject_key ? <>{d.subject_label.replace(` (${d.subject_key})`, "")} (<EmpLink id={d.subject_key} />)</> : d.subject_label} · lập bởi {d.created_by} lúc {formatDateTime(d.created_at)}
            </span>
          ) : (
            "Sau khi lưu nháp có thể xem trước PDF. Biên bản chỉ được cấp số khi chốt."
          )
        }
        actions={
          editable && data && (
            <>
              {!isNew && (
                <a
                  href={dirty ? undefined : `/api/core/documents/${id}/preview.pdf`}
                  target="_blank"
                  rel="noopener"
                  aria-disabled={dirty}
                  className={cn(linkBtn("secondary"), dirty && "pointer-events-none opacity-55")}
                  title={dirty ? "Lưu nháp trước khi xem trước" : undefined}
                >
                  <Printer className="size-4" aria-hidden="true" />
                  Xem trước PDF
                </a>
              )}
              <Button onClick={submitSave} loading={save.isPending} variant={dirty || isNew ? "primary" : "secondary"}>
                <Save className="size-4" aria-hidden="true" />
                Lưu nháp
              </Button>
              {!isNew && (
                <Button variant="primary" disabled={dirty} onClick={() => setFinalizeOpen(true)} title={dirty ? "Lưu nháp trước khi chốt" : undefined}>
                  <Stamp className="size-4" aria-hidden="true" />
                  Chốt biên bản
                </Button>
              )}
            </>
          )
        }
      />

      {d && status !== "draft" && (
        <Panel title="Ký và lưu hồ sơ" className="mb-4">
          <ol className="grid gap-4 lg:grid-cols-3">
            <li className="space-y-2">
              <p className="font-medium">1. In và ký</p>
              <p className="text-sm text-muted">Số <Mono>{d.number}</Mono> · chốt bởi {d.finalized_by} lúc {formatDateTime(d.finalized_at)}</p>
              <div className="flex flex-wrap gap-2">
                <a href={fileUrl("pdf")} target="_blank" rel="noopener" className={linkBtn("secondary")}>
                  <Printer className="size-4" aria-hidden="true" />
                  Mở PDF để in
                </a>
                <a href={fileUrl("docx")} className={linkBtn("secondary")}>
                  <Download className="size-4" aria-hidden="true" />
                  Tải DOCX
                </a>
              </div>
            </li>
            <li className="space-y-2">
              <p className="font-medium">2. Tải lên bản đã ký</p>
              <p className="text-sm text-muted">
                {d.has_signed ? `Đã tải lên bởi ${d.signed_by} lúc ${formatDateTime(d.signed_at)}.` : "File PDF, PNG hoặc JPG, tối đa 20 MB."}
              </p>
              <div className="flex flex-wrap gap-2">
                {d.can_upload_signed && (
                  <>
                    <Button onClick={() => fileRef.current?.click()} loading={uploadSigned.isPending} variant={d.has_signed ? "secondary" : "primary"}>
                      <FileUp className="size-4" aria-hidden="true" />
                      {d.has_signed ? "Thay bản đã ký" : "Tải lên bản đã ký"}
                    </Button>
                    <input
                      ref={fileRef}
                      type="file"
                      accept="application/pdf,image/png,image/jpeg"
                      className="sr-only"
                      tabIndex={-1}
                      aria-hidden="true"
                      onChange={(e) => {
                        const f = e.target.files?.[0];
                        if (f) uploadSigned.mutate(f);
                        e.target.value = "";
                      }}
                    />
                  </>
                )}
                {d.has_signed && (
                  <a href={fileUrl("signed")} target="_blank" rel="noopener" className={linkBtn("secondary")}>
                    <FileCheck2 className="size-4" aria-hidden="true" />
                    Xem bản đã ký
                  </a>
                )}
              </div>
            </li>
            <li className="space-y-2">
              <p className="font-medium">3. Đính kèm vào request SDP</p>
              {status === "signed" ? (
                <form className="flex flex-wrap items-end gap-2" onSubmit={(e) => { e.preventDefault(); attach.mutate(); }}>
                  <Field label="Request ID" className="w-36">
                    <Input value={attachReq} onChange={(e) => setAttachReq(e.target.value.trim())} inputMode="numeric" className="font-mono" />
                  </Field>
                  <Button type="submit" loading={attach.isPending} disabled={!attachReq || !d.can_upload_signed}>
                    <Paperclip className="size-4" aria-hidden="true" />
                    Đính kèm
                  </Button>
                </form>
              ) : (
                <p className="text-sm text-muted">Có thể đính kèm sau khi tải lên bản đã ký. Cần credential Write của ServiceDesk Plus.</p>
              )}
              {d.events.filter((e) => e.type === "sdp_attach").slice(-3).map((e) => (
                <p key={e.at} className={cn("text-sm", e.ok ? "text-done" : "text-failed")}>
                  {e.ok ? `Đã đính kèm vào #${e.request_id}` : `Lỗi đính kèm #${e.request_id}: ${e.error}`} · {formatDateTime(e.at)}
                </p>
              ))}
            </li>
          </ol>
          {status === "void" && <p className="mt-3 rounded-sm bg-skipped-soft px-3 py-2 text-sm">Đã huỷ lúc {formatDateTime(d.voided_at)}. Lý do: {d.void_reason}</p>}
        </Panel>
      )}

      {isNew && !data ? (
        <StartNew kind={kind} onReady={(v, msg) => { setData(v); setDirty(true); setInfo(msg ?? ""); }} />
      ) : (
        data && meta.data && (
          <>
            {info && editable && <p role="status" className="mb-3 rounded-sm bg-progress-soft px-3 py-2 text-sm">{info}</p>}
            <DocForm
              kind={kind}
              value={data}
              onChange={(v) => { setData(v); setDirty(true); }}
              meta={meta.data}
              readOnly={!editable}
              errors={errors}
              showErrors={submitted}
            />
            {serverError && <p role="alert" className="mt-3 rounded-sm bg-failed-soft px-3 py-2 text-sm text-failed">{serverError}</p>}
          </>
        )
      )}

      {d && status !== "void" && (d.can_edit && (status === "draft" || can("lifecycle.templates.manage"))) && (
        <div className="mt-6 flex justify-end">
          <Button variant="ghost" className="text-failed" onClick={() => { setVoidReason(""); setVoidOpen(true); }}>
            <XCircle className="size-4" aria-hidden="true" />
            {status === "draft" ? "Huỷ bản nháp" : "Huỷ biên bản"}
          </Button>
        </div>
      )}

      <Dialog
        open={finalizeOpen}
        onClose={() => setFinalizeOpen(false)}
        title="Chốt biên bản?"
        size="sm"
        footer={
          <>
            <Button onClick={() => setFinalizeOpen(false)}>Để sau</Button>
            <Button variant="primary" loading={finalize.isPending} onClick={() => finalize.mutate()}>Chốt và cấp số</Button>
          </>
        }
      >
        <p>infra-ops sẽ cấp số {kind === "handover" ? "BG" : "TH"}-YYYYMM-#### và tạo PDF theo mẫu đang dùng.</p>
        <p className="mt-2 text-muted">Sau khi chốt, nội dung không sửa được. Nếu sai, huỷ biên bản rồi lập biên bản mới.</p>
      </Dialog>

      <Dialog
        open={voidOpen}
        onClose={() => setVoidOpen(false)}
        title={status === "draft" ? "Huỷ bản nháp?" : `Huỷ biên bản ${d?.number ?? ""}?`}
        size="sm"
        footer={
          <>
            <Button onClick={() => setVoidOpen(false)}>Giữ lại</Button>
            <Button variant="danger" loading={voidDoc.isPending} disabled={voidReason.trim().length < 3} onClick={() => voidDoc.mutate()}>
              {status === "draft" ? "Huỷ bản nháp" : "Huỷ biên bản"}
            </Button>
          </>
        }
      >
        <p className="mb-3 text-sm">
          {status === "draft" ? "Bản nháp sẽ chuyển sang trạng thái Đã huỷ." : "Số biên bản vẫn được giữ trong lịch sử, không cấp lại cho biên bản khác."}
        </p>
        <Field label="Lý do huỷ" required hint="Tối thiểu 3 ký tự. Ghi vào Nhật ký kiểm tra.">
          <Textarea rows={2} value={voidReason} onChange={(e) => setVoidReason(e.target.value)} maxLength={500} />
        </Field>
      </Dialog>
    </>
  );
}
