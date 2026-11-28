import { useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FileSpreadsheet, Trash2, Upload } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { DateInput } from "@/core/ui/date-input";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono, Panel } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge, type Status } from "@/core/ui/status";
import type { Item } from "./StocktakePage";

type Kind = "tscd" | "ccdc";
const KINDS: [Kind, string][] = [["tscd", "Sổ TSCĐ"], ["ccdc", "Sổ CCDC"]];
const KIND_LABEL: Record<Kind, string> = { tscd: "TSCĐ", ccdc: "CCDC" };

/** Money and quantities as the server sends them (decimal strings), vi-VN grouping; "—" when empty. */
export function fmtAmount(v: string | number | null | undefined): string {
  if (v === null || v === undefined || v === "") return "—";
  return new Intl.NumberFormat("vi-VN", { maximumFractionDigits: 2 }).format(Number(v));
}

interface Triple { qty: string | null; cost: string | null; net: string | null }
interface Line {
  kind: Kind;
  code: string;
  name: string;
  place: string;
  book: Triple;
  counted: Triple;
  diff: Triple;
  note: string;
  items: number;
  status: LineStatus;
}
interface OffRegister { name: string; asset_tag: string; serial: string; fa_code: string; place: string; count: string; qty: number }
interface RegisterMeta { rows: number; total_cost: string; as_of: string | null; imported_by: string; imported_at: string }
interface Reconciliation { lines: Line[]; off_register: OffRegister[]; meta: { registers: Partial<Record<Kind, RegisterMeta>>; provisional?: boolean } }
interface Preview {
  sheet: string;
  /** Binds the import to this file + sheet + column choice. */
  preview_token: string;
  /** Every column of the header row: letter, group label (merged top cell) and own label. */
  headers?: { col: string; top: string; sub: string }[];
  /** field → column letter as read (an ambiguous field is absent). */
  mapping?: Record<string, string>;
  /** field → the columns that all carry its label (opening and closing balance): must be chosen by hand. */
  ambiguous?: Record<string, { col: string; label: string }[]>;
  /** Cells that could not be read as money / date: the import is refused while any remain. */
  errors?: { row?: number; column?: string; value?: string; message?: string }[];
  skipped?: Record<string, number>;
  rows: number;
  total_cost: string;
  first_rows: { row_no: number; code: string; name: string; quantity: string | null; cost: string | null; net: string | null }[];
  warnings: string[];
  warnings_total: number;
}

const MAP_FIELDS = ["code", "name", "quantity", "cost", "accumulated", "net", "in_use_date", "department", "account", "serial"] as const;

type LineStatus = "pending" | "match" | "qty_diff" | "missing_all" | "missing_part" | "not_in_sdp";
const LINE_STATUS: Record<LineStatus, { label: string; status: Status }> = {
  pending: { label: "Chưa kiểm", status: "pending" },
  match: { label: "Khớp", status: "match" },
  qty_diff: { label: "Lệch số lượng", status: "mismatch" },
  missing_all: { label: "Không thấy", status: "failed" },
  missing_part: { label: "Thiếu thành phần", status: "warning" },
  not_in_sdp: { label: "Chưa có trên SDP", status: "warning" },
};
const COLUMN_LABEL: Record<string, string> = {
  code: "Mã TSCĐ", name: "Tên", group: "Nhóm", quantity: "Số lượng", cost: "Nguyên giá", accumulated: "Hao mòn luỹ kế",
  net: "Giá trị còn lại", in_use_date: "Ngày đưa vào sử dụng", department: "Bộ phận", life_months: "Thời gian KH", account: "Tài khoản", serial: "Serial",
};
const SHOWN = 200;

/** "Đối chiếu Kế toán": import the accounting register(s) of this stocktake and compare book ↔ SDP ↔ counted. */
export function StocktakeFinance({ stocktakeId, open, canFix, items, onItemSaved }: { stocktakeId: string; open: boolean; canFix: boolean; items: Item[]; onItemSaved: () => void }) {
  const { can } = useAuth();
  const key = ["lifecycle", "stocktake", stocktakeId, "reconciliation"];
  const allowed = can("lifecycle.stocktake.finance");
  const q = useQuery({ queryKey: key, queryFn: () => api<Reconciliation>(`/api/lifecycle/stocktakes/${stocktakeId}/reconciliation`), enabled: allowed });
  const [status, setStatus] = useState("");
  if (!allowed) return null;
  if (q.isLoading) return <LoadingRows />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const { lines, off_register: off, meta } = q.data;
  const table = lines.filter((l) => l.status !== "not_in_sdp" && (!status || l.status === status));
  const notInSdp = lines.filter((l) => l.status === "not_in_sdp");
  const count = (s: LineStatus) => lines.filter((l) => l.status === s).length;
  const imported = Object.keys(meta.registers).length > 0;

  return (
    <div className="grid gap-4">
      <div className="grid gap-4 lg:grid-cols-2">
        {KINDS.map(([kind, title]) => (
          <RegisterCard key={kind} stocktakeId={stocktakeId} kind={kind} title={title} meta={meta.registers[kind] ?? null} open={open} reconciliationKey={key} />
        ))}
      </div>

      {!imported ? (
        <EmptyState icon={FileSpreadsheet} title="Chưa nhập sổ Kế toán">
          Nhập sổ TSCĐ và/hoặc CCDC của Kế toán ở trên để đối chiếu với sổ SDP và kết quả kiểm.
        </EmptyState>
      ) : (
        <>
          <p className="flex flex-wrap items-center gap-x-5 gap-y-1 text-sm tabular">
            {meta.provisional && <StatusBadge status="warning" label="Số liệu tạm" />}
            {count("pending") > 0 && <span>{count("pending")} chưa kiểm</span>}
            <span><b className="font-semibold">{count("match")}</b> khớp</span>
            <span>{count("qty_diff")} lệch số lượng</span>
            <span>{count("missing_part")} thiếu thành phần</span>
            <span>{count("missing_all")} không thấy</span>
            <span>{notInSdp.length} sổ KT chưa có trên SDP</span>
            <span>{off.length} chưa có trong sổ KT</span>
            <HelpTip title="Cách đối chiếu">
              <span className="block">Nối theo Mã TSCĐ (một mã có thể gồm nhiều tài sản IT, ví dụ PC và màn hình). Nguyên giá và giá trị còn lại lấy từ sổ Kế toán; SDP không có giá.</span>
              <span className="block">Sai mã? Sửa Mã TSCĐ của tài sản ở mục “Chưa có trong sổ KT” rồi đối chiếu lại.</span>
            </HelpTip>
          </p>
          <Panel
            title="Đối chiếu Sổ KT – Kiểm kê"
            bodyClassName="p-0"
            actions={
              <Select aria-label="Lọc theo trạng thái" value={status} onChange={(e) => setStatus(e.target.value)} className="w-auto">
                <option value="">Mọi trạng thái</option>
                {(Object.keys(LINE_STATUS) as LineStatus[]).filter((k) => k !== "not_in_sdp").map((k) => <option key={k} value={k}>{LINE_STATUS[k].label}</option>)}
              </Select>
            }
          >
            {table.length === 0 ? (
              <p className="px-4 py-3 text-sm text-muted">Không có dòng nào ở trạng thái này.</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full min-w-[72rem] text-sm">
                  <thead className="text-left text-muted">
                    <tr className="border-b border-line">
                      <th scope="col" rowSpan={2} className="px-4 py-2 font-medium">Tên</th>
                      <th scope="col" rowSpan={2} className="px-2 py-2 font-medium">Mã</th>
                      <th scope="col" rowSpan={2} className="px-2 py-2 font-medium">Nơi sử dụng</th>
                      <th scope="colgroup" colSpan={3} className="px-2 pt-2 text-center font-medium">Theo sổ KT</th>
                      <th scope="colgroup" colSpan={3} className="px-2 pt-2 text-center font-medium">Theo kiểm kê</th>
                      <th scope="colgroup" colSpan={3} className="px-2 pt-2 text-center font-medium">Chênh lệch</th>
                      <th scope="col" rowSpan={2} className="px-2 py-2 font-medium">Trạng thái</th>
                      <th scope="col" rowSpan={2} className="px-4 py-2 font-medium">Ghi chú</th>
                    </tr>
                    <tr className="border-b border-line text-right">
                      {["Sổ", "Kiểm", "CL"].flatMap((g) => ["SL", "NG", "GTCL"].map((c) => <th key={`${g}${c}`} scope="col" className="px-2 pb-2 font-medium"><span className="sr-only">{g === "Sổ" ? "Sổ KT" : g === "Kiểm" ? "Kiểm kê" : "Chênh lệch"} </span>{c}</th>))}
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-line">
                    {table.slice(0, SHOWN).map((l) => (
                      <tr key={`${l.kind}-${l.code}`} className="align-top">
                        <th scope="row" className="px-4 py-2 text-left font-medium">{l.name || "—"} <span className="font-normal text-muted">· {KIND_LABEL[l.kind]}</span></th>
                        <td className="px-2 py-2"><Mono>{l.code}</Mono></td>
                        <td className="px-2 py-2">{l.place || "—"}</td>
                        {[l.book, l.counted, l.diff].flatMap((t, n) => [t.qty, t.cost, t.net].map((v, m) => <td key={`${n}${m}`} className="px-2 py-2 text-right tabular">{fmtAmount(v)}</td>))}
                        <td className="px-2 py-2"><StatusBadge {...LINE_STATUS[l.status]} /></td>
                        <td className="px-4 py-2 text-muted">{l.note}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {table.length > SHOWN && <p className="px-4 py-3 text-sm text-muted">Đang hiện {SHOWN} / {table.length} dòng – lọc theo trạng thái để thu hẹp. File Excel có đủ.</p>}
          </Panel>

          {notInSdp.length > 0 && (
            <Panel title={`Sổ KT chưa có trên SDP (${notInSdp.length})`} description="Có trong sổ Kế toán nhưng không tìm thấy tài sản tương ứng trên SDP: kiểm tra Mã TSCĐ trên SDP hoặc bổ sung tài sản." bodyClassName="p-0">
              <ul className="divide-y divide-line">
                {notInSdp.slice(0, SHOWN).map((l) => (
                  <li key={`${l.kind}-${l.code}`} className="flex flex-wrap items-center gap-x-4 gap-y-1 px-4 py-2 text-sm">
                    <span className="min-w-0 flex-1 font-medium">{l.name || "—"} <span className="font-normal text-muted">· {KIND_LABEL[l.kind]}</span></span>
                    <Mono>{l.code}</Mono>
                    <span className="text-muted">{l.place}</span>
                    <span className="tabular">NG {fmtAmount(l.book.cost)} · GTCL {fmtAmount(l.book.net)}</span>
                  </li>
                ))}
              </ul>
            </Panel>
          )}

          {off.length > 0 && (
            <Panel title={`Chưa có trong sổ KT (${off.length})`} description="Tài sản IT không khớp dòng nào của sổ Kế toán đã nhập. Thường do thiếu hoặc sai Mã TSCĐ trên SDP." bodyClassName="p-0">
              <ul className="divide-y divide-line">
                {off.slice(0, SHOWN).map((o, n) => (
                  <OffRow key={`${o.asset_tag}${o.serial}${n}`} row={o} item={findItem(items, o)} stocktakeId={stocktakeId} canFix={canFix && open} onSaved={() => { onItemSaved(); void q.refetch(); }} />
                ))}
              </ul>
            </Panel>
          )}
        </>
      )}
    </div>
  );
}

/** The stocktake item behind an off-register row (matched on sticker code or serial). */
function findItem(items: Item[], o: OffRegister): Item | null {
  return items.find((i) => (o.asset_tag && i.asset_tag === o.asset_tag) || (o.serial && i.serial === o.serial)) ?? null;
}

function OffRow({ row: o, item, stocktakeId, canFix, onSaved }: { row: OffRegister; item: Item | null; stocktakeId: string; canFix: boolean; onSaved: () => void }) {
  const [code, setCode] = useState(o.fa_code);
  const save = useMutation({
    mutationFn: () => api(`/api/lifecycle/stocktakes/${stocktakeId}/items/${item!.id}/fa-code`, { method: "PATCH", json: { fa_code: code.trim() } }),
    onSuccess: () => { toast.success("Đã lưu Mã TSCĐ."); onSaved(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <li className="flex flex-wrap items-end gap-x-4 gap-y-2 px-4 py-2 text-sm">
      <span className="min-w-0 flex-1">
        <span className="font-medium">{o.name}</span>
        <span className="ml-2 text-muted">Tag <Mono>{o.asset_tag || "—"}</Mono> · Serial <Mono>{o.serial || "—"}</Mono>{o.place && ` · ${o.place}`}</span>
      </span>
      {canFix && item ? (
        <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); if (code.trim() !== o.fa_code) save.mutate(); }}>
          <Field label="Mã TSCĐ"><Input value={code} onChange={(e) => setCode(e.target.value)} aria-label={`Mã TSCĐ ${o.name}`} className="w-40 font-mono" maxLength={128} /></Field>
          <Button type="submit" size="sm" loading={save.isPending} disabled={code.trim() === o.fa_code}>Lưu</Button>
        </form>
      ) : (
        <span>Mã TSCĐ <Mono>{o.fa_code || "—"}</Mono></span>
      )}
    </li>
  );
}

function RegisterCard({ stocktakeId, kind, title, meta, open, reconciliationKey }: { stocktakeId: string; kind: Kind; title: string; meta: RegisterMeta | null; open: boolean; reconciliationKey: unknown[] }) {
  const qc = useQueryClient();
  const fileRef = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<Preview | null>(null);
  const [asOf, setAsOf] = useState("");
  const [mapping, setMapping] = useState<Record<string, string>>({});
  const [badDate, setBadDate] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const base = `/api/lifecycle/stocktakes/${stocktakeId}/registers/${kind}`;
  const reset = () => { setFile(null); setPreview(null); setAsOf(""); setMapping({}); setSentMapping(""); };
  const [sentMapping, setSentMapping] = useState("");
  const detected = preview?.mapping ?? {};
  const dirty = [...new Set([...Object.keys(mapping), ...Object.keys(detected)])].some((k) => (mapping[k] ?? "") !== (detected[k] ?? ""));
  const ambiguous = Object.keys(preview?.ambiguous ?? {});
  const chosen = () => JSON.stringify(Object.fromEntries(Object.entries(mapping).filter(([, v]) => v)));
  const errors = preview?.errors ?? [];
  const form = (f: File, extra: Record<string, string> = {}) => {
    const fd = new FormData();
    fd.set("file", f);
    for (const [k, v] of Object.entries(extra)) fd.set(k, v);
    return fd;
  };
  const doPreview = useMutation({
    mutationFn: ({ f, map }: { f: File; map?: string }) => api<Preview>(`${base}/preview`, { method: "POST", body: form(f, map ? { mapping: map } : {}) }),
    onSuccess: (p, { map }) => { setPreview(p); setSentMapping(map ?? ""); setMapping({ ...(p.mapping ?? {}) }); },
    onError: (e) => { setFile(null); toast.error(errorMessage(e)); },
  });
  const doImport = useMutation({
    mutationFn: () => api(base, { method: "POST", body: form(file!, { confirm: "true", sheet: preview?.sheet ?? "", preview_token: preview!.preview_token, ...(sentMapping ? { mapping: sentMapping } : {}), ...(asOf ? { as_of: asOf } : {}) }) }),
    onSuccess: async () => {
      toast.success(`Đã nhập ${title}.`);
      reset();
      await qc.invalidateQueries({ queryKey: reconciliationKey });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api(base, { method: "DELETE" }),
    onSuccess: async () => {
      toast.success(`Đã xoá ${title}.`);
      setConfirmDelete(false);
      await qc.invalidateQueries({ queryKey: reconciliationKey });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const pick = (f: File | undefined) => { if (f) { setFile(f); setPreview(null); doPreview.mutate({ f }); } };

  return (
    <Panel
      title={title}
      description={meta ? undefined : "Chưa nhập sổ này cho kỳ kiểm kê."}
      actions={open && !preview && (
        <>
          <input ref={fileRef} type="file" accept=".xlsx" className="sr-only" tabIndex={-1} aria-hidden="true" data-testid={`file-${kind}`} onChange={(e) => { const f = e.target.files?.[0]; e.target.value = ""; pick(f); }} />
          <Button size="sm" variant={meta ? "secondary" : "primary"} loading={doPreview.isPending} onClick={() => fileRef.current?.click()}>
            <Upload className="size-4" aria-hidden="true" />{meta ? "Thay bằng file khác" : "Chọn file Excel"}
          </Button>
        </>
      )}
    >
      {meta && (
        <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
          <span className="tabular"><b className="font-semibold">{fmtAmount(meta.rows)}</b> dòng</span>
          <span className="tabular">Tổng nguyên giá <b className="font-semibold">{fmtAmount(meta.total_cost)}</b> đ</span>
          {meta.as_of && <span>Số liệu chốt ngày {formatDate(meta.as_of)}</span>}
          <span className="text-muted">Nhập bởi {meta.imported_by} lúc {formatDateTime(meta.imported_at)}</span>
          {open && (
            <Button size="sm" variant="ghost" onClick={() => setConfirmDelete(true)}><Trash2 className="size-4" aria-hidden="true" />Xoá sổ</Button>
          )}
        </div>
      )}
      {!meta && !preview && !open && <p className="text-sm text-muted">Kỳ đã đóng, không nhập thêm được.</p>}

      {preview && file && (
        <div className="mt-3 grid gap-3 border-t border-line pt-3 first:mt-0 first:border-0 first:pt-0">
          <p className="text-sm"><b className="font-semibold">{file.name}</b> · sheet “{preview.sheet}” · <span className="tabular">{fmtAmount(preview.rows)} dòng · tổng nguyên giá {fmtAmount(preview.total_cost)} đ</span></p>
          <fieldset className="grid gap-2 sm:grid-cols-2">
            <legend className="mb-1 text-sm font-medium">Chọn cột <span className="font-normal text-muted">(app đã tự nhận; sửa nếu sai)</span></legend>
            {MAP_FIELDS.map((f) => (
              <Field key={f} label={COLUMN_LABEL[f] ?? f} required={f === "code"}>
                <Select value={mapping[f] ?? ""} onChange={(e) => setMapping({ ...mapping, [f]: e.target.value })}>
                  <option value="">— không dùng —</option>
                  {(preview.headers ?? []).map((h) => <option key={h.col} value={h.col}>{h.col} – {h.sub}{h.top ? ` (${h.top})` : ""}</option>)}
                </Select>
              </Field>
            ))}
          </fieldset>
          {ambiguous.length > 0 && (
            <div role="alert" className="rounded-md border border-failed bg-failed-soft px-3 py-2 text-sm">
              <p className="font-medium">Có cột trùng nhãn (vd. số dư đầu kỳ và cuối kỳ) – chọn đúng cột rồi bấm “Áp dụng cột đã chọn”.</p>
              <ul className="mt-1 list-disc pl-5">{ambiguous.map((f) => <li key={f}>{COLUMN_LABEL[f] ?? f}: {(preview?.ambiguous?.[f] ?? []).map((c) => `${c.col} – ${c.label}`).join(" · ")}</li>)}</ul>
            </div>
          )}
          {dirty && (
            <div><Button size="sm" loading={doPreview.isPending} onClick={() => doPreview.mutate({ f: file, map: chosen() })}>Áp dụng cột đã chọn</Button></div>
          )}
          {!mapping.code && <p role="alert" className="text-sm text-failed">Chưa chọn cột Mã TSCĐ – kiểm tra dòng tiêu đề của file.</p>}
          {preview.skipped && Object.keys(preview.skipped).length > 0 && (
            <p className="text-sm text-muted">Bỏ qua: {Object.entries(preview.skipped).map(([r, n]) => `${n} dòng ${r}`).join(" · ")}</p>
          )}
          {errors.length > 0 && (
            <div role="alert" className="rounded-md border border-failed bg-failed-soft px-3 py-2 text-sm">
              <p className="font-medium">{errors.length} ô không đọc được – sửa file Excel rồi chọn lại, chưa nhập sổ được.</p>
              <ul className="mt-1 list-disc pl-5">{errors.slice(0, 10).map((er, n) => <li key={n}>{er.row ? `Dòng ${er.row}` : ""}{er.column ? `, cột ${er.column}` : ""}{er.value ? `: “${er.value}”` : ""}{er.message ? ` – ${er.message}` : ""}</li>)}</ul>
              {errors.length > 10 && <p className="mt-1">… và {errors.length - 10} ô khác.</p>}
            </div>
          )}
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <caption className="sr-only">5 dòng đầu của file</caption>
              <thead className="text-left text-muted">
                <tr className="border-b border-line">
                  <th scope="col" className="py-1 pr-3 font-medium">Mã</th>
                  <th scope="col" className="py-1 pr-3 font-medium">Tên</th>
                  <th scope="col" className="py-1 pr-3 text-right font-medium">SL</th>
                  <th scope="col" className="py-1 pr-3 text-right font-medium">Nguyên giá</th>
                  <th scope="col" className="py-1 text-right font-medium">GTCL</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {preview.first_rows.map((r) => (
                  <tr key={r.row_no}>
                    <td className="py-1 pr-3"><Mono>{r.code}</Mono></td>
                    <td className="py-1 pr-3">{r.name}</td>
                    <td className="py-1 pr-3 text-right tabular">{fmtAmount(r.quantity)}</td>
                    <td className="py-1 pr-3 text-right tabular">{fmtAmount(r.cost)}</td>
                    <td className="py-1 text-right tabular">{fmtAmount(r.net)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {preview.warnings_total > 0 && (
            <div role="status" className="rounded-md border border-waiting bg-waiting-soft px-3 py-2 text-sm">
              <p className="font-medium">{preview.warnings_total} cảnh báo khi đọc file{preview.warnings_total > preview.warnings.length ? ` (hiện ${preview.warnings.length} đầu)` : ""}</p>
              <ul className="mt-1 list-disc pl-5">{preview.warnings.slice(0, 10).map((w, n) => <li key={n}>{w}</li>)}</ul>
            </div>
          )}
          <Field label="Ngày chốt số liệu" hint="Ngày Kế toán chốt số liệu của file này (nếu biết)." error="Nhập ngày dạng dd/mm/yyyy." showError={badDate}>
            <DateInput value={asOf} onChange={setAsOf} onInvalidChange={setBadDate} />
          </Field>
          {meta && <p className="text-sm text-muted">Nhập sổ sẽ thay toàn bộ {title} đang có ({fmtAmount(meta.rows)} dòng).</p>}
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" loading={doImport.isPending} disabled={badDate || !mapping.code || errors.length > 0 || ambiguous.length > 0 || dirty} onClick={() => doImport.mutate()}>Nhập sổ</Button>
            <Button onClick={reset}>Huỷ</Button>
          </div>
        </div>
      )}

      <Dialog
        open={confirmDelete}
        onClose={() => setConfirmDelete(false)}
        title={`Xoá ${title}?`}
        description="Xoá toàn bộ các dòng đã nhập của sổ này trong kỳ kiểm kê; có thể nhập lại bằng file khác."
        footer={
          <>
            <Button onClick={() => setConfirmDelete(false)}>Giữ lại</Button>
            <Button variant="danger" loading={remove.isPending} onClick={() => remove.mutate()}>Xoá sổ</Button>
          </>
        }
      />
    </Panel>
  );
}
