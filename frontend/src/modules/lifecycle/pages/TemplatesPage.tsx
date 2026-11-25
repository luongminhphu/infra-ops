import { useRef, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CircleCheck, Download, FileText, TriangleAlert, Upload } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button, buttonVariants } from "@/core/ui/button";
import { ErrorState, LoadingRows, Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { Tabs } from "@/core/ui/tabs";
import { formatDateTime } from "@/core/lib/format";
import { useKinds, type KindInfo } from "../docs";

interface Version {
  version: number;
  is_active: boolean;
  placeholders: string[];
  unknown_placeholders: string[];
  note: string;
  uploaded_by: string | null;
  created_at: string;
  filename: string | null;
  size: number | null;
}

function KindTemplates({ kind }: { kind: KindInfo }) {
  const qc = useQueryClient();
  const versions = useQuery({ queryKey: ["templates", kind.key], queryFn: () => api<Version[]>(`/api/core/document-kinds/${kind.key}/templates`) });
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [note, setNote] = useState("");
  const [activate, setActivate] = useState(false);
  const [error, setError] = useState("");
  const invalidate = () => Promise.all([qc.invalidateQueries({ queryKey: ["templates", kind.key] }), qc.invalidateQueries({ queryKey: ["document-kinds"] })]);

  const upload = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("file", file!);
      fd.append("note", note);
      fd.append("activate", String(activate));
      return api<Version>(`/api/core/document-kinds/${kind.key}/templates`, { method: "POST", body: fd });
    },
    onSuccess: async (v) => {
      await invalidate();
      setFile(null);
      setNote("");
      setError("");
      if (fileRef.current) fileRef.current.value = "";
      toast.success(`Đã tải lên mẫu phiên bản ${v.version}${v.is_active ? " và đang dùng" : ""}.`);
    },
    onError: (e) => setError(errorMessage(e)),
  });
  const activateMut = useMutation({
    mutationFn: (v: number) => api<Version>(`/api/core/document-kinds/${kind.key}/templates/${v}/activate`, { method: "POST" }),
    onSuccess: async (v) => {
      await invalidate();
      toast.success(`Biên bản mới sẽ dùng mẫu phiên bản ${v.version}.`);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!file) {
      setError("Chọn file mẫu .docx.");
      return;
    }
    upload.mutate();
  };
  const link = buttonVariants({ variant: "secondary", size: "sm" });

  return (
    <div className="grid gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
      <div className="space-y-4">
        <Panel title="Các phiên bản mẫu" description="Biên bản đã chốt luôn giữ nguyên phiên bản mẫu lúc chốt." bodyClassName="p-0">
          {versions.isLoading && <LoadingRows rows={2} />}
          {versions.isError && <div className="p-4"><ErrorState error={versions.error} /></div>}
          <ul className="divide-y divide-line">
            {versions.data?.map((v) => (
              <li key={v.version} className="flex flex-wrap items-start gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <p className="flex flex-wrap items-center gap-2 font-medium">
                    Phiên bản {v.version}
                    {v.is_active && <StatusBadge status="done" label="Đang dùng" />}
                  </p>
                  <p className="text-sm text-muted">
                    {v.filename} · {v.uploaded_by} · {formatDateTime(v.created_at)}
                    {v.note && ` · ${v.note}`}
                  </p>
                  {v.unknown_placeholders.length > 0 && (
                    <p className="mt-1 flex items-start gap-1.5 text-sm text-waiting">
                      <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                      Placeholder không có dữ liệu (sẽ để trống): {v.unknown_placeholders.map((p) => <Mono key={p} className="mr-1">{p}</Mono>)}
                    </p>
                  )}
                </div>
                <div className="flex flex-wrap gap-2">
                  <a className={link} href={`/api/core/document-kinds/${kind.key}/templates/${v.version}/sample.pdf`} target="_blank" rel="noopener">
                    <FileText className="size-4" aria-hidden="true" />
                    Xem với dữ liệu mẫu
                  </a>
                  <a className={link} href={`/api/core/document-kinds/${kind.key}/templates/${v.version}/file`}>
                    <Download className="size-4" aria-hidden="true" />
                    Tải .docx
                  </a>
                  {!v.is_active && (
                    <Button size="sm" onClick={() => activateMut.mutate(v.version)} loading={activateMut.isPending && activateMut.variables === v.version}>
                      <CircleCheck className="size-4" aria-hidden="true" />
                      Dùng phiên bản này
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        </Panel>
        <Panel title="Tải lên mẫu mới" description="Tải mẫu đang dùng về, chỉnh trong Word (giữ nguyên các placeholder), rồi tải lên thành phiên bản mới.">
          <form onSubmit={submit} className="space-y-3" noValidate>
            <Field label="File mẫu (.docx)" required error={error || undefined} showError={!!error}>
              <Input
                ref={fileRef}
                type="file"
                accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                onChange={(e) => { setFile(e.target.files?.[0] ?? null); setError(""); }}
                className="h-auto py-1.5"
              />
            </Field>
            <Field label="Ghi chú thay đổi">
              <Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={500} placeholder="Ví dụ: cập nhật logo, thêm mục kiểm tra" />
            </Field>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={activate} onChange={(e) => setActivate(e.target.checked)} />
              Dùng ngay cho biên bản mới
            </label>
            <Button type="submit" variant="primary" loading={upload.isPending}>
              <Upload className="size-4" aria-hidden="true" />
              Tải lên
            </Button>
          </form>
        </Panel>
      </div>
      <Panel title="Hướng dẫn placeholder" description="Gõ placeholder trong Word đúng như cột bên trái, kể cả dấu ngoặc nhọn.">
        <div className="mb-3 space-y-1.5 rounded-sm bg-surface-2 p-3 text-sm">
          <p>Giá trị: <Mono>{"{{ employee.full_name }}"}</Mono></p>
          <p>
            Bảng thiết bị: một dòng chứa <Mono>{"{%tr for a in assets %}"}</Mono>, một dòng dữ liệu dùng <Mono>{"{{ a.name }}"}</Mono>…, một dòng chứa{" "}
            <Mono>{"{%tr endfor %}"}</Mono>. Hai dòng điều khiển sẽ bị xoá khi in.
          </p>
          <p>Ô đánh dấu: <Mono>{"{{ checklist[0].ok }}"}</Mono> in ra ☑ hoặc ☐ (đếm từ 0).</p>
        </div>
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Danh sách placeholder</caption>
          <thead className="text-muted">
            <tr>
              <th scope="col" className="py-1.5 font-semibold">Placeholder</th>
              <th scope="col" className="py-1.5 font-semibold">Ý nghĩa</th>
            </tr>
          </thead>
          <tbody>
            {kind.placeholders.map((p) => (
              <tr key={p.name} className="border-t border-line align-top">
                <td className="py-1.5 pr-3"><Mono className="break-all">{`{{ ${p.name} }}`}</Mono></td>
                <td className="py-1.5">
                  {p.description}
                  {p.example && <span className="block text-xs text-muted">Ví dụ: {p.example}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Panel>
    </div>
  );
}

export function TemplatesPage() {
  const kinds = useKinds();
  const mine = (kinds.data ?? []).filter((k) => k.key.startsWith("lifecycle.") && k.can_manage);
  const [tab, setTab] = useState("lifecycle.handover");
  return (
    <>
      <PageHeader title="Mẫu biên bản" description="Quản lý mẫu Word dùng để in Biên bản Bàn giao và Biên bản Thu hồi. Mỗi lần tải lên tạo một phiên bản mới, không ghi đè phiên bản cũ." />
      {kinds.isError && <ErrorState error={kinds.error} onRetry={() => kinds.refetch()} />}
      {kinds.isLoading && <LoadingRows rows={4} />}
      {mine.length > 0 && (
        <Tabs label="Loại biên bản" value={tab} onChange={setTab} tabs={mine.map((k) => ({ key: k.key, label: `${k.title} (${k.prefix})` }))}>
          {mine.filter((k) => k.key === tab).map((k) => <KindTemplates key={k.key} kind={k} />)}
        </Tabs>
      )}
    </>
  );
}
