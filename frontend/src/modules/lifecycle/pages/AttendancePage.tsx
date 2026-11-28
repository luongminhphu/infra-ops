import { useMemo, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Download, FileSpreadsheet, ScanSearch } from "lucide-react";
import { toast } from "sonner";
import { fold, formatDateTime } from "@/core/lib/format";
import { useDebounced } from "@/core/lib/useDebounced";
import { api, downloadFile, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { DateInput } from "@/core/ui/date-input";
import { Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge, type Status } from "@/core/ui/status";

const MAX_DAYS = 62;
const MAX_IDS = 500;
// Taller controls on phones (44px touch targets).
const TOUCH = "max-sm:h-11";

/** IDs in a plain list, by the server's rule for lists without a header: 4–8 digits, leading zeros restored. */
export function parseIds(text: string): string[] {
  const ids = text
    .split(/[\s,;]+/)
    .map((t) => t.replace(/^[<>()[\]{}"']+|[<>()[\]{}"']+$/g, ""))
    .filter((t) => /^\d{4,8}$/.test(t))
    .map((t) => t.padStart(6, "0"));
  return [...new Set(ids)];
}

/** Text that is more than a list of IDs (a table copied from an e-mail or Excel, IDs mixed with names). */
export function looksLikeTable(text: string): boolean {
  return text.includes("\t") || /\p{L}/u.test(text);
}

/** Stable string for a set of IDs: same content gives the same string (a new array each render must not retrigger debounce). */
export function idsKey(ids: string[]): string {
  return [...ids].sort().join(",");
}

/** Existing IDs plus new ones, in order, once each. */
export function mergeIds(existing: string[], added: string[]): string[] {
  return [...new Set([...existing, ...added])];
}

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export const fmtVn = (isoDate: string) => isoDate.split("-").reverse().join("/");

/** [from, to] of this month (up to today) or of last month. */
export function monthRange(which: "this" | "last", now = new Date()): [string, string] {
  if (which === "this") return [iso(new Date(now.getFullYear(), now.getMonth(), 1)), iso(now)];
  return [iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)), iso(new Date(now.getFullYear(), now.getMonth(), 0))];
}

/** Validation of the period; each message belongs to the field it is shown under. */
export function periodErrors(from: string, to: string, fromBad: boolean, toBad: boolean, today: string) {
  const fromError = fromBad || !from ? "Nhập ngày dạng dd/mm/yyyy." : from > today ? "Ngày bắt đầu không được ở tương lai." : "";
  const days = from && to ? Math.round((Date.parse(to) - Date.parse(from)) / 86_400_000) + 1 : 0;
  const toError =
    toBad || !to
      ? "Nhập ngày dạng dd/mm/yyyy."
      : fromError
        ? ""
        : to < from
          ? "Không được trước ngày bắt đầu."
          : days > MAX_DAYS
            ? `Tối đa ${MAX_DAYS} ngày mỗi lần (đang chọn ${days} ngày).`
            : "";
  return { fromError, toError, days };
}

/** Name compared without accents, punctuation or word order (same rule as the server's name_key). */
export function nameKey(name: string): string {
  return (fold(name).match(/\p{L}+|\d+/gu) ?? []).sort().join(" ");
}

export interface PreviewItem {
  employee_id: string;
  sdp_name: string | null;
  biostar: { name: string; user_id: string; source: string | null; active: boolean }[];
  status: "ok" | "name_differs" | "sdp_only" | "biostar_only" | "not_found" | "unknown";
}
interface Preview {
  synced_at: string | null;
  failed_sources: string[];
  items: PreviewItem[];
}

const PREVIEW_BADGE: Record<PreviewItem["status"], [Status, string]> = {
  ok: ["match", "Khớp"],
  name_differs: ["mismatch", "Lệch tên"],
  sdp_only: ["warning", "Chỉ có ở SDP"],
  biostar_only: ["warning", "Chỉ có ở BioStar"],
  not_found: ["failed", "Chưa thấy"],
  unknown: ["skipped", "Chưa rõ"],
};

/** "38 đúng · 2 lệch tên · 1 chưa thấy" (other groups only when present). */
export function previewSummary(items: { status: PreviewItem["status"] }[]): string {
  const n = (...st: PreviewItem["status"][]) => items.filter((i) => st.includes(i.status)).length;
  const parts = [`${n("ok")} đúng`, `${n("name_differs")} lệch tên`, `${n("not_found")} chưa thấy`];
  if (n("sdp_only", "biostar_only")) parts.push(`${n("sdp_only", "biostar_only")} chỉ có ở một hệ thống`);
  if (n("unknown")) parts.push(`${n("unknown")} chưa rõ`);
  return parts.join(" · ");
}

interface Recognized {
  items: { employee_id: string; name: string }[];
  total: number;
  skipped: number;
  column: string | null;
  max: number;
}

interface Result {
  file: string;
  from: string;
  to: string;
  requested: number;
  notFound: number;
  notFoundIds: string[];
  readOk: number;
  readErrors: string[];
  sources: string[]; // per BioStar: IDs matched on the last 5 digits and scans read
  error?: string; // the export failed: nothing was downloaded
}

export function AttendancePage() {
  const { can } = useAuth();
  const [ids, setIds] = useState("");
  // The exact IDs in the box when they came from recognising a table or file (null once the person edits the box).
  const [known, setKnown] = useState<string[] | null>(null);
  const [found, setFound] = useState<Recognized | null>(null);
  const [reading, setReading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [[from, to], setPeriod] = useState(() => monthRange("this"));
  const [fromBad, setFromBad] = useState(false);
  const [toBad, setToBad] = useState(false);
  // Bumped by a preset: the date boxes start over from the new period, even over a half-typed date.
  const [presetRun, setPresetRun] = useState(0);
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const status = useQuery({
    queryKey: ["lifecycle", "attendance", "status"],
    queryFn: () => api<{ ready: boolean; message: string | null }>("/api/lifecycle/attendance/status"),
  });
  const notReady = status.data?.ready === false;

  const count = known ? known.length : looksLikeTable(ids) ? null : parseIds(ids).length;
  const idError =
    count === null
      ? "Bảng chưa được đọc – bấm “Nhận diện Mã NV”."
      : count === 0
        ? "Nhập ít nhất một Mã NV (4–8 chữ số)."
        : count > MAX_IDS
          ? `Tối đa ${MAX_IDS} Mã NV mỗi lần (đang có ${count}).`
          : "";
  // Check of the IDs against the last synced SDP / BioStar data; never blocks the export.
  // Debounce a string, not an array: a fresh array every render would restart the timer forever.
  const debouncedKey = useDebounced(idsKey(known ?? (looksLikeTable(ids) ? [] : parseIds(ids))), 400);
  const previewKey = useMemo(() => (debouncedKey ? debouncedKey.split(",") : []), [debouncedKey]);
  const preview = useQuery({
    queryKey: ["lifecycle", "attendance", "preview", previewKey],
    queryFn: () => api<Preview>("/api/lifecycle/attendance/preview", { method: "POST", json: { employee_ids: `Mã NV\n${previewKey.join("\n")}` } }),
    enabled: count !== null && !idError && previewKey.length > 0,
  });
  const hrNames = new Map((known && found ? found.items : []).map((i) => [i.employee_id, i.name]));
  const { fromError, toError } = periodErrors(from, to, fromBad, toBad, iso(new Date()));
  const valid = !idError && !fromError && !toError;

  const setBox = (text: string, list: string[] | null, rec: Recognized | null) => {
    setIds(text);
    setKnown(list);
    setFound(rec);
  };

  // HR's list (file or pasted table) → the server recognises the ID column. Added to the IDs already in the box,
  // unless the box itself is the table being read.
  const recognize = async (body: FormData, label: string, merge: boolean) => {
    setReading(true);
    const before = { text: ids, list: known, rec: found };
    try {
      const r = await api<Recognized>("/api/lifecycle/attendance/parse", { method: "POST", body });
      const existing = merge ? (known ?? (looksLikeTable(ids) ? [] : parseIds(ids))) : [];
      const list = mergeIds(existing, r.items.map((i) => i.employee_id));
      const added = list.length - existing.length;
      setBox(list.join("\n"), list, r);
      const skipped = r.skipped ? ` (bỏ qua ${r.skipped} dòng không có mã)` : "";
      toast.success(
        existing.length ? `Thêm ${added} Mã NV từ ${label}${skipped} · tổng ${list.length}.` : `Đã nhận diện ${r.total} Mã NV từ ${label}${skipped}.`,
        existing.length ? { action: { label: "Hoàn tác", onClick: () => setBox(before.text, before.list, before.rec) } } : undefined,
      );
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setReading(false);
    }
  };
  const textForm = (text: string) => {
    const fd = new FormData();
    fd.set("text", text);
    return fd;
  };

  const run = async () => {
    setTouched(true);
    if (!valid || notReady) return;
    setBusy(true);
    // Recognised IDs go with a header row, so IDs with letters are read as IDs too.
    const employee_ids = known ? `Mã NV\n${known.join("\n")}` : ids;
    try {
      const h = await downloadFile("/api/lifecycle/attendance/export", { employee_ids, date_from: from, date_to: to }, "cham-cong.xlsx");
      const detail = decodeURIComponent(h.get("X-Read-Error-Detail") ?? "");
      const r: Result = {
        file: /filename="([^"]+)"/.exec(h.get("Content-Disposition") ?? "")?.[1] ?? "cham-cong.xlsx",
        from,
        to,
        requested: Number(h.get("X-Requested") ?? count ?? 0),
        notFound: Number(h.get("X-Not-Found") ?? 0),
        notFoundIds: (h.get("X-Not-Found-Ids") ?? "").split(",").filter(Boolean),
        readOk: Number(h.get("X-Read-Ok") ?? 0),
        readErrors: detail ? detail.split("\n") : [],
        sources: decodeURIComponent(h.get("X-Source-Detail") ?? "").split("\n").filter(Boolean),
      };
      setResult(r);
      if (r.readErrors.length) toast.warning("Đã xuất file, nhưng chưa đủ dữ liệu – xem kết quả.");
      else toast.success("Đã xuất báo cáo chấm công.");
    } catch (e) {
      toast.error(errorMessage(e));
      setResult({ file: "", from, to, requested: count ?? 0, notFound: 0, notFoundIds: [], readOk: 0, readErrors: [], sources: [], error: errorMessage(e) });
    } finally {
      setBusy(false);
    }
  };

  const period = !fromError && !toError ? `${fmtVn(from)} – ${fmtVn(to)}` : "";
  const preset = (which: "this" | "last") => {
    setPeriod(monthRange(which));
    setFromBad(false);
    setToBad(false);
    setPresetRun((n) => n + 1);
  };

  return (
    <>
      <PageHeader
        title="Báo cáo chấm công"
        description="Đọc log quẹt thẻ / vân tay / khuôn mặt trên mọi hệ thống BioStar theo Mã NV và kỳ HR yêu cầu, rồi xuất file Excel."
      />
      <div className="grid items-start gap-4 xl:grid-cols-[minmax(0,44rem)_minmax(0,1fr)]">
        <div className="grid gap-4">
          {notReady && (
            <div role="alert" className="flex flex-wrap items-start gap-3 rounded-md border border-waiting bg-waiting-soft px-4 py-3">
              <StatusBadge status="warning" label="Chưa xuất được" />
              <p className="min-w-0 flex-1 text-sm text-pretty">
                {status.data?.message} Báo cáo cần đọc BioStar.{" "}
                {can("core.connections.manage") ? (
                  <Link to="/settings/connections" className="font-medium text-primary underline">Thêm kết nối BioStar</Link>
                ) : (
                  "Báo Administrator thêm kết nối BioStar ở Hệ thống › Kết nối."
                )}
              </p>
            </div>
          )}
          <Panel
            title="Yêu cầu rà soát"
            actions={
              <HelpTip title="Cách dùng và cách tính">
                <span className="block">Mỗi lần tối đa {MAX_IDS} Mã NV và {MAX_DAYS} ngày. Mã NV được khớp với user BioStar theo 5 số cuối, như ở Đối soát.</span>
                <span className="block">Sheet “Tổng hợp theo ngày”: mỗi nhân viên một dòng mỗi ngày, giờ vào là lần quẹt thành công đầu tiên, giờ ra là lần cuối cùng trong ngày (giờ Việt Nam). Ngày không có lượt quẹt vẫn được liệt kê.</span>
                <span className="block">Sheet “Log chi tiết”: từng lượt quẹt thành công kèm thiết bị. Ca làm qua đêm chưa được gộp.</span>
              </HelpTip>
            }
          >
            <form
              className="grid gap-4"
              onSubmit={(e) => {
                e.preventDefault();
                void run();
              }}
            >
              <Field
                label="Mã nhân viên"
                hint={
                  count === null
                    ? "Ô có chữ hoặc nhiều cột – bấm “Nhận diện Mã NV” để app tìm cột Mã NV."
                    : `Dán nguyên bảng từ email/Excel của HR (app tự tìm cột Mã NV) hoặc gõ danh sách mã. Đang có ${count} Mã NV.`
                }
                error={idError}
                showError={touched}
                required
              >
                <Textarea
                  rows={6}
                  value={ids}
                  onChange={(e) => setBox(e.target.value, null, null)}
                  onPaste={(e) => {
                    const text = e.clipboardData.getData("text/plain");
                    if (!looksLikeTable(text)) return;
                    e.preventDefault();
                    void recognize(textForm(text), "bảng đã dán", true);
                  }}
                  placeholder="Dán bảng từ email của HR, hoặc ví dụ: 003551, 003475"
                  className="font-mono placeholder:font-sans"
                />
              </Field>
              <div className="flex flex-wrap items-center gap-2">
                <input
                  ref={fileRef}
                  type="file"
                  accept=".xlsx,.xlsm,.csv,.txt"
                  className="sr-only"
                  tabIndex={-1}
                  aria-hidden="true"
                  onChange={(e) => {
                    const f = e.target.files?.[0];
                    e.target.value = "";
                    if (!f) return;
                    const fd = new FormData();
                    fd.set("file", f);
                    void recognize(fd, `file ${f.name}`, true);
                  }}
                />
                <Button className={TOUCH} onClick={() => fileRef.current?.click()} loading={reading}>
                  {!reading && <FileSpreadsheet className="size-4" aria-hidden="true" />}Nhập file Excel / CSV
                </Button>
                {count === null && (
                  <Button className={TOUCH} onClick={() => void recognize(textForm(ids), "bảng trong ô", false)} loading={reading}>
                    {!reading && <ScanSearch className="size-4" aria-hidden="true" />}Nhận diện Mã NV
                  </Button>
                )}
              </div>
              {count !== null && !idError && (
                <section className="grid gap-2 rounded-md border border-line bg-surface-2 px-3 py-2 text-sm" aria-label="Kiểm tra Mã NV theo dữ liệu đồng bộ">
                  {preview.isError ? (
                    <StatusBadge status="warning" label="Chưa kiểm tra được Mã NV" />
                  ) : !preview.data ? (
                    <span className="text-muted">Đang kiểm tra Mã NV…</span>
                  ) : (
                    <>
                      <b className="font-semibold">{previewSummary(preview.data.items)}</b>
                      <ul className="max-h-56 overflow-y-auto" tabIndex={0} aria-label="Kết quả kiểm tra từng Mã NV">
                        {preview.data.items.map((i) => {
                          const [st, label] = PREVIEW_BADGE[i.status];
                          const hr = hrNames.get(i.employee_id);
                          const ref = i.sdp_name ?? i.biostar[0]?.name;
                          return (
                            <li key={i.employee_id} className="flex flex-wrap items-center gap-x-2 gap-y-1 py-0.5">
                              <Mono>{i.employee_id}</Mono>
                              <span>SDP: {i.sdp_name ?? "—"}</span>
                              <span className="text-muted">BioStar: {i.biostar.length ? i.biostar.map((b) => b.name).join(", ") : "—"}</span>
                              {hr && <span className="text-muted">HR: {hr}</span>}
                              {hr && ref && nameKey(hr) !== nameKey(ref) && <StatusBadge status="mismatch" label="Lệch tên HR" />}
                              <StatusBadge status={st} label={label} />
                              {i.biostar.some((b) => !b.active) && <StatusBadge status="warning" label="BioStar đã khoá" />}
                            </li>
                          );
                        })}
                      </ul>
                      <span className="text-muted">
                        {preview.data.synced_at ? `Theo dữ liệu đồng bộ lúc ${formatDateTime(preview.data.synced_at)}` : "Chưa có lần đồng bộ nào."}
                        {preview.data.failed_sources.length > 0 && " · Một số nguồn đọc lỗi ở lần đồng bộ này."}
                      </span>
                    </>
                  )}
                </section>
              )}
              {found && (
                <details className="rounded-md border border-line bg-surface-2 px-3 py-2 text-sm">
                  <summary className="cursor-pointer">
                    <b className="font-semibold">Lần nhận diện gần nhất: {found.total} nhân viên</b>
                    {found.column ? ` từ cột “${found.column}”` : " (mã 4–8 chữ số trong từng dòng)"}
                    {found.skipped > 0 && ` · bỏ qua ${found.skipped} dòng không có mã`}
                    {found.total > found.max && ` · chỉ lấy ${found.max} người đầu`}
                  </summary>
                  <ul className="mt-2 max-h-56 columns-1 gap-6 overflow-y-auto sm:columns-2" tabIndex={0} aria-label="Nhân viên đã nhận diện">
                    {found.items.map((i) => (
                      <li key={i.employee_id} className="break-inside-avoid py-0.5"><Mono>{i.employee_id}</Mono>{i.name && <span className="text-muted"> · {i.name}</span>}</li>
                    ))}
                  </ul>
                </details>
              )}
              <fieldset className="grid gap-2">
                <legend className="sr-only">Kỳ báo cáo</legend>
                <div className="flex flex-wrap gap-2" role="group" aria-label="Chọn nhanh kỳ">
                  <Button size="sm" className={TOUCH} onClick={() => preset("last")}>Tháng trước</Button>
                  <Button size="sm" className={TOUCH} onClick={() => preset("this")}>Tháng này</Button>
                </div>
                <div className="flex flex-wrap items-start gap-4">
                  <Field label="Từ ngày" required error={fromError} showError={touched || fromBad} className="w-52">
                    <DateInput key={`from-${presetRun}`} value={from} onChange={(v) => setPeriod([v, to])} onInvalidChange={setFromBad} className={`w-36 tabular ${TOUCH}`} />
                  </Field>
                  <Field label="Đến ngày" required error={toError} showError={touched || toBad} className="w-52">
                    <DateInput key={`to-${presetRun}`} value={to} onChange={(v) => setPeriod([from, v])} onInvalidChange={setToBad} className={`w-36 tabular ${TOUCH}`} />
                  </Field>
                </div>
              </fieldset>
              <div>
                <Button type="submit" variant="primary" className={TOUCH} loading={busy} disabled={notReady}>
                  {!busy && <Download className="size-4" aria-hidden="true" />}
                  {busy ? "Đang đọc BioStar…" : valid ? `Xuất Excel · ${count} NV · ${period}` : "Xuất Excel"}
                </Button>
              </div>
            </form>
          </Panel>
        </div>
        <ResultPanel result={result} busy={busy} onAgain={() => void run()} />
      </div>
    </>
  );
}

/** What went into the last file – stays on the page so the reply to HR can be checked and written from it. */
function ResultPanel({ result, busy, onAgain }: { result: Result | null; busy: boolean; onAgain: () => void }) {
  if (!result)
    return (
      <Panel title="Kết quả">
        <p className="text-sm text-muted text-pretty">
          Sau khi xuất, kỳ báo cáo, số nhân viên tìm thấy và Mã NV không có trên BioStar hiện ở đây để bạn kiểm tra trước khi gửi file cho HR.
        </p>
      </Panel>
    );
  if (result.error)
    return (
      <Panel
        title="Kết quả lần xuất"
        actions={<Button size="sm" onClick={onAgain} loading={busy}>{!busy && <Download className="size-4" aria-hidden="true" />}Thử lại</Button>}
      >
        <div role="alert" className="grid gap-2 text-sm">
          <StatusBadge status="failed" label="Không xuất được" />
          <p className="text-pretty">{result.error}</p>
          <p className="text-muted text-pretty">Chưa có file nào được tải về. Kiểm tra kết nối BioStar ở Hệ thống › Kết nối hệ thống rồi thử lại.</p>
        </div>
      </Panel>
    );
  const partial = result.readErrors.length > 0;
  const foundCount = result.requested - result.notFound;
  return (
    <Panel
      title="Kết quả lần xuất"
      actions={<Button size="sm" onClick={onAgain} loading={busy}>{!busy && <Download className="size-4" aria-hidden="true" />}Xuất lại</Button>}
    >
      <div role="status" className="grid gap-3 text-sm">
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={partial ? "warning" : result.notFound ? "mismatch" : "done"} label={partial ? "Chưa đủ dữ liệu" : result.notFound ? "Có người không tìm thấy" : "Đủ dữ liệu"} />
          <Mono>{result.file}</Mono>
        </div>
        <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1">
          <dt className="text-muted">Kỳ</dt>
          <dd className="tabular">{fmtVn(result.from)} – {fmtVn(result.to)}</dd>
          <dt className="text-muted">Nhân viên</dt>
          <dd className="tabular">{foundCount} / {result.requested} có trên BioStar</dd>
          <dt className="text-muted">BioStar đã đọc</dt>
          <dd className="tabular">{result.readOk} hệ thống{partial && `, ${result.readErrors.length} lỗi`}</dd>
        </dl>
        {result.sources.length > 0 && (
          <ul className="grid gap-1 rounded-md bg-surface-2 px-3 py-2" aria-label="Chi tiết từng hệ thống BioStar">
            {result.sources.map((src) => <li key={src} className="text-pretty">{src}</li>)}
          </ul>
        )}
        {partial && (
          <div className="rounded-md border border-waiting bg-waiting-soft px-3 py-2">
            <p className="font-medium">File thiếu log của các hệ thống sau – đừng gửi HR như bản đầy đủ:</p>
            <ul className="mt-1 list-disc pl-5">
              {result.readErrors.map((e) => <li key={e}>{e}</li>)}
            </ul>
          </div>
        )}
        {result.notFound > 0 && (
          <div>
            <p>
              <b className="font-semibold">{result.notFound} Mã NV không có trên BioStar</b> (cũng ghi trong sheet Ghi chú):
            </p>
            <p className="mt-1 flex flex-wrap gap-x-3 gap-y-1">
              {result.notFoundIds.map((id) => <Mono key={id}>{id}</Mono>)}
              {result.notFound > result.notFoundIds.length && <span className="text-muted">và {result.notFound - result.notFoundIds.length} mã khác</span>}
            </p>
          </div>
        )}
      </div>
    </Panel>
  );
}
