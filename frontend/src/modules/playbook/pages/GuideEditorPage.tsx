import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, ImagePlus, Plus, Save, Send, Trash2, X } from "lucide-react";
import { toast } from "sonner";
import { api, ApiError, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { formatDateTime } from "@/core/lib/format";
import { getGuide, newId, pruneUnreachable, removeStep, unreachableSteps, type Fact, type GuideDetail, type Tree } from "../api";
import { Mindmap } from "../Mindmap";
import { Note } from "../Note";
import { RunPanel } from "../Run";
import { StepKindEditor } from "../StepKindEditor";

export function GuideEditorPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["playbook", "guide", id, "draft"], queryFn: () => getGuide(id, "draft") });
  const [tree, setTree] = useState<Tree | null>(null);
  const [meta, setMeta] = useState({ title: "", category: "", description: "" });
  const [base, setBase] = useState("");
  const [dirty, setDirty] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [publishOpen, setPublishOpen] = useState(false);
  const [publishNote, setPublishNote] = useState("");
  const [removeOpen, setRemoveOpen] = useState(false);
  const [preview, setPreview] = useState(false);
  const [path, setPath] = useState<Set<string>>(new Set());
  const facts = useQuery({ queryKey: ["playbook", "facts"], queryFn: () => api<Fact[]>("/api/playbook/facts"), staleTime: Infinity });
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  const load = (g: GuideDetail) => {
    setTree(g.tree);
    setMeta({ title: g.title, category: g.category, description: g.description });
    setBase(g.draft_updated_at ?? "");
    setDirty(false);
    setSelected((s) => (s && g.tree.nodes[s] ? s : g.tree.root));
  };
  useEffect(() => {
    if (q.data && !dirty) load(q.data);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [q.data]);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => e.preventDefault();
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const change = (next: Tree) => { setTree(next); setDirty(true); };
  const save = useMutation({
    mutationFn: () => api<GuideDetail>(`/api/playbook/guides/${id}/draft`, { method: "PUT", json: { ...meta, tree, base_updated_at: base } }),
    onSuccess: (g) => { load(g); toast.success("Đã lưu bản nháp. Người đọc vẫn thấy bản đã xuất bản cho đến khi xuất bản lại."); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const publish = useMutation({
    mutationFn: () => api(`/api/playbook/guides/${id}/publish`, { method: "POST", json: { note: publishNote } }),
    onSuccess: async () => {
      setPublishOpen(false);
      await qc.invalidateQueries({ queryKey: ["playbook"] });
      toast.success("Đã xuất bản. IT Support thấy phiên bản mới ngay.");
      navigate(`/playbook/${id}`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const upload = useMutation({
    mutationFn: (f: File) => { const fd = new FormData(); fd.set("file", f); return api<{ url: string }>(`/api/playbook/guides/${id}/images`, { method: "POST", body: fd }); },
    onSuccess: (r) => insert(`\n![Ảnh minh hoạ](${r.url})\n`),
    onError: (e) => toast.error(e instanceof ApiError && e.code === "unsupported_file" ? "Chỉ nhận ảnh PNG hoặc JPEG." : errorMessage(e)),
  });

  if (q.isLoading || (!tree && !q.isError)) return <LoadingRows rows={8} />;
  if (q.isError || !tree) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const sel = selected && tree.nodes[selected] ? selected : tree.root;
  const node = tree.nodes[sel]!;
  const setNode = (patch: Partial<typeof node>) => change({ ...tree, nodes: { ...tree.nodes, [sel]: { ...node, ...patch } } });
  function insert(text: string) {
    const el = noteRef.current;
    const at = el?.selectionStart ?? node.note.length;
    setNode({ note: node.note.slice(0, at) + text + node.note.slice(at) });
  }
  const addChild = () => {
    const nid = newId();
    const label = node.kind === "check" ? (node.children.length ? "Sai" : "Đúng") : "";
    change({ ...tree, nodes: { ...tree.nodes, [sel]: { ...node, children: [...node.children, { label, to: nid }] }, [nid]: { title: "Bước mới", note: "", children: [] } } });
    setSelected(nid);
  };
  const others = Object.entries(tree.nodes).filter(([k]) => k !== sel);
  const addJump = () => others[0] && setNode({ children: [...node.children, { label: node.kind === "check" ? (node.children.length ? "Sai" : "Đúng") : "", to: others[0][0] }] });
  const afterRemove = removeStep(tree, sel);
  const removing = Object.keys(tree.nodes).length - Object.keys(afterRemove.nodes).length;
  const lost = unreachableSteps(tree);

  return (
    <>
      <nav aria-label="Đường dẫn">
        <Link to={q.data?.published_version ? `/playbook/${id}` : "/playbook"} className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
          <ChevronLeft className="size-4" aria-hidden="true" />{q.data?.published_version ? "Xem bản đã xuất bản" : "Infra Playbook"}
        </Link>
      </nav>
      <PageHeader
        title={`Soạn: ${meta.title || "hướng dẫn"}`}
        description={
          <>
            Bản nháp{q.data?.draft_updated_by ? ` – lưu lần cuối ${formatDateTime(base)} bởi ${q.data.draft_updated_by}` : ""}.{" "}
            {q.data?.published_version ? `Đang dùng phiên bản ${q.data.published_version}.` : "Chưa xuất bản."}
            {dirty && <span className="ml-1 font-medium text-waiting">Có thay đổi chưa lưu.</span>}
          </>
        }
        actions={
          <span className="inline-flex flex-wrap gap-2">
            <Button variant={dirty ? "primary" : "secondary"} loading={save.isPending} onClick={() => save.mutate()} disabled={!meta.title.trim()}>
              {!save.isPending && <Save className="size-4" aria-hidden="true" />}Lưu nháp
            </Button>
            {can("playbook.guides.publish") && (
              <Button variant={dirty ? "secondary" : "primary"} disabled={dirty} title={dirty ? "Lưu nháp trước khi xuất bản" : undefined} onClick={() => { setPublishNote(""); setPublishOpen(true); }}>
                <Send className="size-4" aria-hidden="true" />Xuất bản
              </Button>
            )}
          </span>
        }
      />
      <div className="mb-4 grid gap-3 md:grid-cols-[2fr_1fr]">
        <Field label="Tên hướng dẫn" required error={meta.title.trim() ? "" : "Nhập tên hướng dẫn."} showError>
          <Input value={meta.title} maxLength={200} onChange={(e) => { setMeta({ ...meta, title: e.target.value }); setDirty(true); }} />
        </Field>
        <Field label="Nhóm">
          <Input value={meta.category} maxLength={100} onChange={(e) => { setMeta({ ...meta, category: e.target.value }); setDirty(true); }} />
        </Field>
        <Field label="Mô tả ngắn" className="md:col-span-2">
          <Input value={meta.description} maxLength={1000} onChange={(e) => { setMeta({ ...meta, description: e.target.value }); setDirty(true); }} placeholder="Khi nào dùng hướng dẫn này" />
        </Field>
      </div>
      {lost.length > 0 && (
        <div role="status" className="mb-3 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-sm bg-waiting-soft px-3 py-2 text-sm">
          <p className="min-w-0 flex-1 text-pretty">
            Có {lost.length} bước không còn nhánh nào trỏ tới nên không hiện trên sơ đồ và sẽ bị từ chối khi xuất bản:{" "}
            <strong>{lost.map((k) => tree.nodes[k]!.title || "(chưa đặt tên)").join(", ")}</strong>. Cần dùng lại thì chọn nó ở “Tới bước” của một nhánh; không cần thì xoá.
          </p>
          <Button size="sm" variant="danger" onClick={() => change(pruneUnreachable(tree))}>
            <Trash2 className="size-4" aria-hidden="true" />Xoá {lost.length} bước không nối
          </Button>
        </div>
      )}

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_440px]">
        <div className="space-y-4">
          <Mindmap tree={tree} selected={sel} onSelect={setSelected} label="Sơ đồ đang soạn" path={path} />
          {dirty ? (
            <p className="text-sm text-muted">Lưu nháp để chạy thử bản nháp với một Mã NV.</p>
          ) : (
            <RunPanel guideId={id} tree={tree} version="draft" onPath={setPath} onSelect={setSelected} />
          )}
        </div>
        <Panel
          title={sel === tree.root ? "Bước gốc" : "Bước đang chọn"}
          actions={
            <HelpTip title="Định dạng ghi chú">
              <span className="block">**chữ đậm**, `lệnh`, dòng bắt đầu bằng “- ” hoặc “1. ” thành danh sách, [chữ](https://… hoặc /lifecycle/jobs) thành liên kết.</span>
              <span className="block">Dán ảnh chụp màn hình (Ctrl+V) vào ô ghi chú hoặc bấm “Chèn ảnh” (PNG/JPEG).</span>
            </HelpTip>
          }
          className="xl:sticky xl:top-4 xl:self-start"
        >
          <div className="grid gap-3">
            <Field label="Tiêu đề bước" required>
              <Input value={node.title} maxLength={200} onChange={(e) => setNode({ title: e.target.value })} />
            </Field>
            <StepKindEditor node={node} facts={facts.data ?? []} onChange={setNode} />
            <div>
              <div className="mb-1 flex items-center justify-between gap-2">
                <span className="text-sm font-medium">Ghi chú</span>
                <span className="inline-flex gap-1">
                  <Button size="sm" variant="ghost" onClick={() => setPreview((p) => !p)} aria-pressed={preview}>{preview ? "Sửa" : "Xem trước"}</Button>
                  <Button size="sm" variant="ghost" loading={upload.isPending} onClick={() => fileRef.current?.click()}><ImagePlus className="size-4" aria-hidden="true" />Chèn ảnh</Button>
                </span>
              </div>
              <input ref={fileRef} type="file" accept="image/png,image/jpeg" className="sr-only" aria-label="Chọn ảnh minh hoạ" onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; if (f) upload.mutate(f); }} />
              {preview ? (
                <div className="min-h-40 rounded-sm border border-line bg-surface p-3"><Note text={node.note} /></div>
              ) : (
                <Textarea
                  ref={noteRef}
                  rows={9}
                  aria-label="Ghi chú của bước"
                  value={node.note}
                  maxLength={20000}
                  onChange={(e) => setNode({ note: e.target.value })}
                  onPaste={(e) => {
                    const img = [...e.clipboardData.files].find((f) => f.type.startsWith("image/"));
                    if (img) { e.preventDefault(); upload.mutate(img); }
                  }}
                  placeholder="Mô tả việc cần làm ở bước này, quy định cần tuân theo, liên kết tới trang liên quan…"
                />
              )}
            </div>
            <fieldset className="grid gap-2">
              <legend className="mb-1 text-sm font-medium">Nhánh tiếp theo ({node.children.length})</legend>
              {node.children.length === 0 && <p className="text-sm text-muted">Bước kết thúc – chưa có nhánh.</p>}
              {node.children.map((b, i) => (
                <div key={i} className="grid grid-cols-[1fr_1.4fr_auto] items-end gap-2">
                  <Field label={`Lựa chọn ${i + 1}`}>
                    <Input value={node.kind === "check" ? (i === 0 ? "Đúng" : "Sai") : b.label} readOnly={node.kind === "check"} maxLength={100} placeholder="Có / Không…" onChange={(e) => setNode({ children: node.children.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })} />
                  </Field>
                  <Field label="Tới bước">
                    <Select value={b.to} onChange={(e) => setNode({ children: node.children.map((x, j) => (j === i ? { ...x, to: e.target.value } : x)) })}>
                      {others.map(([k, n]) => <option key={k} value={k}>{n.title}</option>)}
                    </Select>
                  </Field>
                  <Button variant="ghost" aria-label={`Bỏ nhánh ${i + 1}`} onClick={() => setNode({ children: node.children.filter((_, j) => j !== i) })}><X className="size-4" aria-hidden="true" /></Button>
                </div>
              ))}
              <span className="flex flex-wrap gap-2">
                <Button size="sm" onClick={addChild} disabled={node.kind === "check" && node.children.length >= 2}><Plus className="size-4" aria-hidden="true" />Thêm bước con</Button>
                <Button size="sm" onClick={addJump} disabled={!others.length || (node.kind === "check" && node.children.length >= 2)}>Nhánh tới bước có sẵn</Button>
              </span>
            </fieldset>
            {sel !== tree.root && (
              <div className="border-t border-line pt-3">
                <Button size="sm" variant="danger" onClick={() => setRemoveOpen(true)}><Trash2 className="size-4" aria-hidden="true" />Xoá bước</Button>
              </div>
            )}
          </div>
        </Panel>
      </div>

      <Dialog
        open={removeOpen}
        onClose={() => setRemoveOpen(false)}
        title={`Xoá bước “${node.title}”?`}
        size="sm"
        description={removing > 1 ? `Xoá luôn ${removing - 1} bước chỉ nối qua bước này. Các nhánh trỏ tới bước bị xoá cũng bị bỏ. Chưa lưu thì vẫn tải lại được bản nháp cũ.` : "Các nhánh trỏ tới bước này cũng bị bỏ."}
        footer={
          <>
            <Button onClick={() => setRemoveOpen(false)}>Giữ lại</Button>
            <Button variant="danger" onClick={() => { change(afterRemove); setSelected(tree.root); setRemoveOpen(false); }}>Xoá {removing} bước</Button>
          </>
        }
      />
      <Dialog
        open={publishOpen}
        onClose={() => setPublishOpen(false)}
        title="Xuất bản hướng dẫn"
        description={`Tạo phiên bản ${(q.data?.published_version ?? 0) + 1}; IT Support thấy ngay. Phiên bản cũ vẫn xem lại được.`}
        footer={
          <>
            <Button onClick={() => setPublishOpen(false)}>Huỷ</Button>
            <Button variant="primary" loading={publish.isPending} onClick={() => publish.mutate()}><Send className="size-4" aria-hidden="true" />Xuất bản</Button>
          </>
        }
      >
        <Field label="Thay đổi trong phiên bản này" hint="Ghi vào lịch sử phiên bản (ISO 27001).">
          <Textarea rows={3} maxLength={1000} value={publishNote} onChange={(e) => setPublishNote(e.target.value)} placeholder="Ví dụ: thêm nhánh xử lý máy Mac" />
        </Field>
      </Dialog>
    </>
  );
}
