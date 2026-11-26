import { useRef, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, ClipboardCheck, Download, PackagePlus, ScanLine } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { Button, buttonVariants } from "@/core/ui/button";
import { DateInput } from "@/core/ui/date-input";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge, type Status } from "@/core/ui/status";
import { Tabs } from "@/core/ui/tabs";

export type Count = "pending" | "found" | "missing" | "extra";
type Condition = "good" | "damaged" | "broken";

export interface Item {
  id: number;
  asset_id: string | null;
  name: string;
  product_type: string | null;
  asset_tag: string | null;
  serial: string | null;
  fa_code: string | null;
  state: string | null;
  user_name: string | null;
  site: string | null;
  count: Count;
  condition: Condition | null;
  seen_at: string;
  note: string;
  counted_by: string | null;
  resolution: string | null;
  resolution_note: string;
  difference: string | null;
}

interface Summary {
  book: number;
  pending: number;
  found: number;
  missing: number;
  extra: number;
  differences: number;
  unresolved: number;
}

interface Head {
  id: string;
  name: string;
  cutoff: string;
  status: "counting" | "reviewing" | "closed";
  status_label: string;
  source_synced_at: string | null;
  created_by: string;
  closed_at: string | null;
}

const COUNT: Record<Count, { label: string; status: Status }> = {
  pending: { label: "Chưa kiểm", status: "pending" },
  found: { label: "Có", status: "done" },
  missing: { label: "Không tìm thấy", status: "failed" },
  extra: { label: "Ngoài sổ SDP", status: "mismatch" },
};
const CONDITION: Record<Condition, string> = { good: "Tốt", damaged: "Hư hỏng nhẹ", broken: "Hỏng, không dùng được" };
const STATUS: Record<Head["status"], Status> = { counting: "in_progress", reviewing: "waiting", closed: "done" };
const SHOWN = 200;

export type View = "all" | "pending" | "missing" | "found" | "differences" | "extra";

/** Items of one tab, narrowed by a search over name, codes, user and site. */
export function viewItems(items: Item[], view: View, query: string): Item[] {
  const q = query.trim().toLowerCase();
  return items.filter(
    (i) =>
      (view === "all" ||
        (view === "differences" ? Boolean(i.difference) : view === "found" ? i.count === "found" : i.count === view)) &&
      (!q || [i.name, i.asset_tag, i.serial, i.fa_code, i.user_name, i.site].some((v) => v?.toLowerCase().includes(q))),
  );
}

// ---------------------------------------------------------------- list

export function StocktakesPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["lifecycle", "stocktakes"],
    queryFn: () => api<{ items: (Head & { book: number })[]; last_sync: string | null }>("/api/lifecycle/stocktakes"),
  });
  return (
    <>
      <PageHeader
        title="Kiểm kê tài sản IT"
        description="Kiểm kê cuối năm: chốt sổ tài sản từ SDP, kiểm thực tế từng máy, xử lý chênh lệch, rồi gửi file cho Hành chánh và Kế toán đối chiếu."
        actions={can("lifecycle.stocktake.manage") && <Button variant="primary" onClick={() => setOpen(true)}><ClipboardCheck className="size-4" aria-hidden="true" />Mở kỳ kiểm kê</Button>}
      />
      {q.isLoading && <LoadingRows />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.items.length === 0 && (
        <EmptyState icon={ClipboardCheck} title="Chưa có kỳ kiểm kê">
          Supervisor/Manager mở kỳ kiểm kê; app chốt sổ theo lần đồng bộ SDP gần nhất ({formatDateTime(q.data.last_sync, "chưa đồng bộ")}).
        </EmptyState>
      )}
      {q.data && q.data.items.length > 0 && (
        <ul className="divide-y divide-line border-y border-line">
          {q.data.items.map((s) => (
            <li key={s.id}>
              <Link to={`/lifecycle/stocktake/${s.id}`} className="flex min-h-14 flex-wrap items-center gap-x-4 gap-y-1 px-1 py-2 hover:text-primary">
                <span className="min-w-0 flex-1 font-medium">{s.name}</span>
                <span className="text-sm text-muted tabular">Chốt sổ {formatDate(s.cutoff)} · {s.book} tài sản</span>
                <StatusBadge status={STATUS[s.status]} label={s.status_label} />
                <ChevronRight className="size-4 text-muted" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      )}
      <CreateDialog open={open} onClose={() => setOpen(false)} lastSync={q.data?.last_sync ?? null} onCreated={(id) => navigate(`/lifecycle/stocktake/${id}`)} />
    </>
  );
}

function CreateDialog({ open, onClose, lastSync, onCreated }: { open: boolean; onClose: () => void; lastSync: string | null; onCreated: (id: string) => void }) {
  const year = new Date().getFullYear();
  const [name, setName] = useState(`Kiểm kê tài sản IT ${year}`);
  const [cutoff, setCutoff] = useState(`${year}-12-31`);
  const [bad, setBad] = useState(false);
  const qc = useQueryClient();
  const create = useMutation({
    mutationFn: () => api<Head>("/api/lifecycle/stocktakes", { method: "POST", json: { name, cutoff } }),
    onSuccess: async (s) => {
      await qc.invalidateQueries({ queryKey: ["lifecycle", "stocktakes"] });
      onClose();
      onCreated(s.id);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Mở kỳ kiểm kê"
      description={<>Sổ sách là danh sách tài sản IT của lần đồng bộ SDP gần nhất ({formatDateTime(lastSync, "chưa đồng bộ")}). Đồng bộ lại trước nếu SDP vừa thay đổi.</>}
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" loading={create.isPending} disabled={!name.trim() || !cutoff || bad} onClick={() => create.mutate()}>Chốt sổ và mở kỳ</Button>
        </>
      }
    >
      <div className="grid gap-4">
        <Field label="Tên kỳ" required>
          <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
        </Field>
        <Field label="Ngày chốt sổ" required error="Nhập ngày dạng dd/mm/yyyy." showError={bad}>
          <DateInput value={cutoff} onChange={setCutoff} onInvalidChange={setBad} />
        </Field>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------- one stocktake

export function StocktakePage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const key = ["lifecycle", "stocktake", id];
  const q = useQuery({ queryKey: key, queryFn: () => api<Head & { summary: Summary; items: Item[] }>(`/api/lifecycle/stocktakes/${id}`) });
  const [view, setView] = useState<View>("differences");
  const [touchedView, setTouchedView] = useState(false);
  const [query, setQuery] = useState("");
  const [extra, setExtra] = useState<{ code: string } | null>(null);
  const refresh = () => qc.invalidateQueries({ queryKey: key });

  const step = useMutation({
    mutationFn: (path: "finish-count" | "close") => api(`/api/lifecycle/stocktakes/${id}/${path}`, { method: "POST" }),
    onSuccess: async (_r, path) => {
      toast.success(path === "close" ? "Đã đóng kỳ kiểm kê." : "Đã kết thúc kiểm – xử lý các chênh lệch.");
      setView("differences");
      await refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (q.isLoading) return <LoadingRows />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const s = q.data;
  const open = s.status !== "closed";
  const counting = can("lifecycle.stocktake.count") && open;
  const manage = can("lifecycle.stocktake.manage");
  // Counting starts on what is left to count; afterwards on the differences.
  const current: View = view === "differences" && s.status === "counting" && !touchedView ? "pending" : view;
  const shown = viewItems(s.items, current, query);
  const tabs: { key: View; label: string; n: number }[] = [
    s.status === "counting"
      ? { key: "pending", label: "Chưa kiểm", n: s.summary.pending }
      : { key: "missing", label: "Không tìm thấy", n: s.summary.missing },
    { key: "found", label: "Đã thấy", n: s.summary.found },
    { key: "differences", label: "Chênh lệch", n: s.summary.differences },
    { key: "extra", label: "Ngoài sổ SDP", n: s.summary.extra },
    { key: "all", label: "Tất cả", n: s.items.length },
  ];

  return (
    <>
      <PageHeader
        title={s.name}
        description={<>Chốt sổ {formatDate(s.cutoff)} · dữ liệu SDP lúc {formatDateTime(s.source_synced_at)} · mở bởi {s.created_by}</>}
        actions={
          <>
            <a href={`/api/lifecycle/stocktakes/${id}/export`} className={buttonVariants({ variant: "secondary" })}><Download className="size-4" aria-hidden="true" />Xuất Excel</a>
            {manage && s.status === "counting" && <Button variant="primary" loading={step.isPending} onClick={() => step.mutate("finish-count")}>Kết thúc kiểm</Button>}
            {manage && s.status === "reviewing" && (
              <Button variant="primary" loading={step.isPending} disabled={s.summary.unresolved > 0} onClick={() => step.mutate("close")}>
                Đóng kỳ{s.summary.unresolved > 0 ? ` (còn ${s.summary.unresolved} chênh lệch)` : ""}
              </Button>
            )}
          </>
        }
      />
      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <StatusBadge status={STATUS[s.status]} label={s.status_label} />
        <span className="tabular"><b className="font-semibold">{s.summary.found}</b> / {s.summary.book} tài sản trên sổ đã thấy</span>
        {s.summary.extra > 0 && <span className="tabular">{s.summary.extra} ngoài sổ SDP</span>}
        <span className="tabular">{s.summary.differences} chênh lệch · {s.summary.unresolved} chưa có hướng xử lý</span>
        <HelpTip title="Các bước kiểm kê">
          <span className="block">1. Đang kiểm: quét hoặc gõ serial / asset tag / Mã TSCĐ của từng máy (hoặc bấm Có trong danh sách). Máy không có trên SDP: Thêm tài sản ngoài sổ.</span>
          <span className="block">2. Kết thúc kiểm (Supervisor/Manager): máy chưa thấy thành “Không tìm thấy”. Vẫn sửa được nếu tìm ra sau.</span>
          <span className="block">3. Chọn hướng xử lý cho mọi chênh lệch (đã thống nhất với Hành chánh / Kế toán), rồi Đóng kỳ. File Excel có sheet Sổ SDP để HC/KT đối chiếu với sổ của mình.</span>
        </HelpTip>
      </div>
      {counting && <ScanBox stocktakeId={id} onCounted={refresh} onUnknown={(code) => setExtra({ code })} />}
      <Panel
        title="Tài sản"
        actions={counting && <Button size="sm" onClick={() => setExtra({ code: "" })}><PackagePlus className="size-4" aria-hidden="true" />Thêm tài sản ngoài sổ</Button>}
        bodyClassName="p-0"
      >
        <div className="px-4 pt-3">
          <Tabs label="Lọc tài sản" value={current} onChange={(k) => { setTouchedView(true); setView(k as View); }} tabs={tabs.map((t) => ({ key: t.key, label: <>{t.label} <span className="tabular text-muted">{t.n}</span></> }))}>
            <div className="py-3">
              <Input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tìm tên, serial, asset tag, Mã TSCĐ, người dùng, site" aria-label="Tìm tài sản" className="max-w-md" />
            </div>
          </Tabs>
        </div>
        {shown.length === 0 ? (
          <p className="px-4 pb-4 text-sm text-muted">Không có tài sản nào ở mục này.</p>
        ) : (
          <ul className="divide-y divide-line border-t border-line">
            {shown.slice(0, SHOWN).map((i) => (
              <ItemRow key={i.id} item={i} stocktakeId={id} status={s.status} canCount={counting} canResolve={manage && open} onSaved={refresh} />
            ))}
          </ul>
        )}
        {shown.length > SHOWN && <p className="px-4 py-3 text-sm text-muted">Đang hiện {SHOWN} / {shown.length} – tìm để thu hẹp.</p>}
      </Panel>
      {extra && <ExtraDialog stocktakeId={id} code={extra.code} onClose={() => setExtra(null)} onSaved={refresh} />}
    </>
  );
}

function ScanBox({ stocktakeId, onCounted, onUnknown }: { stocktakeId: string; onCounted: () => void; onUnknown: (code: string) => void }) {
  const [code, setCode] = useState("");
  const [seenAt, setSeenAt] = useState("");
  const [last, setLast] = useState<{ tone: Status; text: string; code?: string } | null>(null);
  const ref = useRef<HTMLInputElement>(null);
  const scan = useMutation({
    mutationFn: (c: string) => api<{ matched: boolean; already: boolean; item: Item | null }>(`/api/lifecycle/stocktakes/${stocktakeId}/scan`, { method: "POST", json: { code: c, seen_at: seenAt } }),
    onSuccess: (r, c) => {
      if (!r.matched) setLast({ tone: "warning", text: `“${c}” không có trong sổ SDP của kỳ này.`, code: c });
      else if (r.already) setLast({ tone: "skipped", text: `${r.item!.name} – đã kiểm trước đó (${r.item!.counted_by ?? "—"}).` });
      else setLast({ tone: "done", text: `${r.item!.name} – đã ghi nhận Có.` });
      setCode("");
      onCounted();
      ref.current?.focus();
    },
    onError: (e) => setLast({ tone: "failed", text: errorMessage(e) }),
  });
  return (
    <Panel title="Quét tài sản" className="mb-4">
      <form
        className="flex flex-wrap items-end gap-3"
        onSubmit={(e) => {
          e.preventDefault();
          if (code.trim()) scan.mutate(code.trim());
        }}
      >
        <Field label="Serial / asset tag / Mã TSCĐ" hint="Máy quét mã vạch gõ mã rồi Enter; gõ tay cũng được." className="min-w-64 flex-1">
          <Input ref={ref} value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" autoFocus className="font-mono max-sm:h-11" />
        </Field>
        <Field label="Nơi kiểm" hint="Giữ nguyên cho các máy tiếp theo." className="w-56">
          <Input value={seenAt} onChange={(e) => setSeenAt(e.target.value)} placeholder="vd. Tầng 3 – P.Kế toán" className="max-sm:h-11" />
        </Field>
        <Button type="submit" variant="primary" loading={scan.isPending} className="max-sm:h-11">{!scan.isPending && <ScanLine className="size-4" aria-hidden="true" />}Ghi nhận</Button>
      </form>
      {last && (
        <p role="status" className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <StatusBadge status={last.tone} label={last.tone === "done" ? "Có" : last.tone === "warning" ? "Không có trong sổ" : last.tone === "failed" ? "Lỗi" : "Đã kiểm"} />
          <span>{last.text}</span>
          {last.code && <Button size="sm" variant="link" onClick={() => onUnknown(last.code!)}>Thêm tài sản ngoài sổ</Button>}
        </p>
      )}
    </Panel>
  );
}

function ItemRow({ item: i, stocktakeId, status, canCount, canResolve, onSaved }: { item: Item; stocktakeId: string; status: Head["status"]; canCount: boolean; canResolve: boolean; onSaved: () => void }) {
  const meta = useQuery({ queryKey: ["lifecycle", "stocktake-meta"], queryFn: () => api<{ resolutions: Record<string, string> }>("/api/lifecycle/stocktake-meta"), staleTime: Infinity });
  const [note, setNote] = useState(i.resolution_note);
  const save = useMutation({
    mutationFn: (body: { count: Count; condition?: Condition | null }) =>
      api(`/api/lifecycle/stocktakes/${stocktakeId}/items/${i.id}`, { method: "PUT", json: { ...body, seen_at: i.seen_at, note: i.note } }),
    onSuccess: onSaved,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const resolve = useMutation({
    mutationFn: (resolution: string | null) => api(`/api/lifecycle/stocktakes/${stocktakeId}/items/${i.id}/resolution`, { method: "PUT", json: { resolution, note } }),
    onSuccess: onSaved,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/lifecycle/stocktakes/${stocktakeId}/extras/${i.id}`, { method: "DELETE" }),
    onSuccess: onSaved,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const book = i.count !== "extra";
  return (
    <li className="grid gap-2 px-4 py-3">
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{i.name}</span>
          <StatusBadge {...COUNT[i.count]} />
          {i.condition && i.condition !== "good" && <StatusBadge status="warning" label={CONDITION[i.condition]} />}
        </p>
        <p className="mt-0.5 flex flex-wrap gap-x-4 text-sm text-muted">
          {i.product_type && <span>{i.product_type}</span>}
          <span>Tag <Mono>{i.asset_tag || "—"}</Mono></span>
          <span>Serial <Mono>{i.serial || "—"}</Mono></span>
          <span>Mã TSCĐ <Mono>{i.fa_code || "—"}</Mono></span>
          {i.user_name && <span>{i.user_name}</span>}
          {i.site && <span>Site {i.site}</span>}
          {i.seen_at && <span>Thấy ở {i.seen_at}</span>}
        </p>
        {i.difference && <p className="mt-1 text-sm">Chênh lệch: {i.difference}{i.resolution && meta.data && <> · <b className="font-semibold">{meta.data.resolutions[i.resolution]}</b>{i.resolution_note && ` – ${i.resolution_note}`}</>}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2 empty:hidden">
        {canCount && book && (
          <>
            {i.count !== "found" && <Button size="sm" className="max-sm:h-11" loading={save.isPending} onClick={() => save.mutate({ count: "found", condition: "good" })}>Có</Button>}
            {i.count === "found" && (
              <Select aria-label={`Tình trạng ${i.name}`} value={i.condition ?? "good"} onChange={(e) => save.mutate({ count: "found", condition: e.target.value as Condition })} className="h-7 w-auto max-sm:h-11">
                {Object.entries(CONDITION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            )}
            {i.count !== "missing" && status === "reviewing" && <Button size="sm" variant="ghost" className="max-sm:h-11" onClick={() => save.mutate({ count: "missing" })}>Không tìm thấy</Button>}
            {i.count === "found" && status === "counting" && <Button size="sm" variant="ghost" className="max-sm:h-11" onClick={() => save.mutate({ count: "pending" })}>Bỏ đánh dấu</Button>}
          </>
        )}
        {canCount && !book && <Button size="sm" variant="ghost" className="max-sm:h-11" loading={remove.isPending} onClick={() => remove.mutate()}>Xoá dòng</Button>}
        {canResolve && i.difference && status !== "counting" && meta.data && (
          <>
            <Input value={note} onChange={(e) => setNote(e.target.value)} onBlur={() => i.resolution && note !== i.resolution_note && resolve.mutate(i.resolution)} placeholder="Ghi chú xử lý" aria-label={`Ghi chú xử lý ${i.name}`} className="h-7 w-48 max-sm:h-11" maxLength={1000} />
            <Select aria-label={`Hướng xử lý ${i.name}`} value={i.resolution ?? ""} onChange={(e) => resolve.mutate(e.target.value || null)} className="h-7 w-auto max-sm:h-11">
              <option value="">Chọn hướng xử lý…</option>
              {Object.entries(meta.data.resolutions).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
            </Select>
          </>
        )}
      </div>
    </li>
  );
}

function ExtraDialog({ stocktakeId, code, onClose, onSaved }: { stocktakeId: string; code: string; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ name: "", product_type: "", serial: code, asset_tag: "", fa_code: "", condition: "good" as Condition, seen_at: "", note: "" });
  const [touched, setTouched] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const nameError = f.name.trim() ? "" : "Nhập tên tài sản.";
  const codeError = f.serial.trim() || f.asset_tag.trim() || f.fa_code.trim() ? "" : "Nhập ít nhất serial, asset tag hoặc Mã TSCĐ.";
  const add = useMutation({
    mutationFn: () => api(`/api/lifecycle/stocktakes/${stocktakeId}/extras`, { method: "POST", json: f }),
    onSuccess: () => {
      toast.success("Đã thêm tài sản ngoài sổ.");
      onSaved();
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog
      open
      onClose={onClose}
      title="Thêm tài sản ngoài sổ SDP"
      description="Thiết bị có trên thực tế nhưng không có trong sổ SDP của kỳ này. Sau khi kết thúc kiểm, chọn hướng xử lý (thường là Ghi tăng tài sản)."
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" loading={add.isPending} onClick={() => { setTouched(true); if (!nameError && !codeError) add.mutate(); }}>Thêm</Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Tên tài sản" required error={nameError} showError={touched} className="sm:col-span-2"><Input value={f.name} onChange={set("name")} maxLength={256} /></Field>
        <Field label="Serial" error={codeError} showError={touched}><Input value={f.serial} onChange={set("serial")} className="font-mono" maxLength={128} /></Field>
        <Field label="Asset tag"><Input value={f.asset_tag} onChange={set("asset_tag")} className="font-mono" maxLength={128} /></Field>
        <Field label="Mã TSCĐ"><Input value={f.fa_code} onChange={set("fa_code")} className="font-mono" maxLength={128} /></Field>
        <Field label="Loại"><Input value={f.product_type} onChange={set("product_type")} placeholder="vd. Laptop" maxLength={128} /></Field>
        <Field label="Tình trạng">
          <Select value={f.condition} onChange={set("condition")}>
            {Object.entries(CONDITION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <Field label="Nơi thấy"><Input value={f.seen_at} onChange={set("seen_at")} maxLength={256} /></Field>
        <Field label="Ghi chú" className="sm:col-span-2"><Input value={f.note} onChange={set("note")} maxLength={1000} /></Field>
      </div>
    </Dialog>
  );
}
