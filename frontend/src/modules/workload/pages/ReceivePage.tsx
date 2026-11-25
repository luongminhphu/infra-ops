import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { CheckCircle2, ChevronLeft, FileSpreadsheet, LoaderCircle, PackageCheck, TriangleAlert } from "lucide-react";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { isSettled } from "@/core/lib/jobs";
import { Button } from "@/core/ui/button";
import { EmptyState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { announce } from "@/core/ui/live-region";
import { Wizard } from "@/core/ui/wizard";
import type { Purchase } from "../api";
import { todayVn } from "../periods";
import { assignSerials, suggestTag, typeCode, type HandoverRow, type Unit } from "../receive";

const STEPS = [
  { key: "po", title: "Chọn PO" },
  { key: "scan", title: "Serial từng máy" },
  { key: "confirm", title: "Xác nhận nhập kho" },
  { key: "done", title: "Kết quả" },
];

interface ReceivablePo {
  id: string;
  number: string | null;
  closed_at: string | null;
  items: Purchase[];
}
interface Target {
  connection: { name: string; environment: string; enabled: boolean } | null;
  state: string;
  asset_tag: { prefix: string; codes: Record<string, string> };
}
const DRAFT_KEY = "infraops.receive.units";
const loadDraft = (): Record<string, Unit[]> => {
  try {
    return JSON.parse(sessionStorage.getItem(DRAFT_KEY) ?? "{}") as Record<string, Unit[]>;
  } catch {
    return {};
  }
};
interface Allocation {
  requests: { id: string; display_id: string | null; full_name: string | null; effective_date: string | null; status: string; job: { id: string } | null; lines: { label: string; buy: number }[] }[];
}

// The SDP receive job, read from core jobs: operators follow and retry it here (the proposals list
// only shows them their own proposals).
interface ReceiveJob {
  id: string;
  status: string;
  steps: { message: string | null; output: { assets?: unknown[] } }[];
}
type Result = { id: string; name: string; ok: boolean; message?: string; job?: string; received?: boolean };

const waitingItems = (po: ReceivablePo) => po.items.filter((p) => p.status === "ordered" && !p.receive_job);

/**
 * Receive the goods of a closed PO in one pass (operators unpack the boxes): pick the PO, scan a serial
 * per device or read them from the vendor's handover file, take the suggested asset tags, confirm the
 * SDP write, then see what onboards can now be equipped. Entered serials survive a refresh (session).
 */
export function ReceivePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { can } = useAuth();
  const [i, setI] = useState(0);
  const [poId, setPoId] = useState(params.get("po") ?? "");
  const [units, setUnits] = useState<Record<string, Unit[]>>(loadDraft);
  const [fileNote, setFileNote] = useState<{ ok: boolean; text: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  useEffect(() => {
    try {
      sessionStorage.setItem(DRAFT_KEY, JSON.stringify(units));
    } catch {
      /* private mode: the draft just isn't kept */
    }
  }, [units]);
  const [tried, setTried] = useState(false);
  const [results, setResults] = useState<Result[]>([]);
  const pos = useQuery({ queryKey: ["workload", "receivable"], queryFn: () => api<{ closed: ReceivablePo[] }>("/api/workload/purchases/receivable") });
  const target = useQuery({ queryKey: ["workload", "receive-target"], queryFn: () => api<Target>("/api/workload/purchases/receive-target") });
  const open = (pos.data?.closed ?? []).filter((po) => waitingItems(po).length > 0);
  // Pre-select the most recently closed PO (goods usually arrive in that order); the person confirms.
  const po = open.find((x) => x.id === poId) ?? open[0];
  const items = po ? waitingItems(po) : [];
  const tracked = items.filter((p) => p.sdp_product);
  const unitsOf = (p: Purchase) => units[p.id] ?? Array.from({ length: p.quantity }, (): Unit => ({ serial: "", asset_tag: "" }));
  const allSerials = tracked.flatMap((p) => unitsOf(p).map((u) => u.serial.trim().toLowerCase()));
  const dupes = useMemo(() => new Set(allSerials.filter((s, k) => s && allSerials.indexOf(s) !== k)), [allSerials]);
  const missing = allSerials.filter((s) => s.length < 2).length;
  const allTags = tracked.flatMap((p) => unitsOf(p).map((u) => u.asset_tag.trim().toLowerCase()));
  const tagDupes = new Set(allTags.filter((t, k) => t && allTags.indexOf(t) !== k));
  const year = todayVn().slice(0, 4);
  const tagFor = (p: Purchase, serial: string) =>
    target.data ? suggestTag(target.data.asset_tag.prefix, year, typeCode(p.catalog_item?.category, target.data.asset_tag.codes), serial) : "";
  const conn = target.data?.connection;
  const noSdp = open.some((x) => waitingItems(x).some((p) => p.sdp_product)) && target.isSuccess && !conn?.enabled;
  const noSdpText = conn ? "Kết nối ServiceDesk Plus đang tắt." : "Chưa có kết nối ServiceDesk Plus.";
  const fieldIds = tracked.flatMap((p) => unitsOf(p).map((_, k) => `rcv-${p.id}-${k}`));

  // Results step: follow the SDP jobs until each proposal is received or failed.
  const jobIds = results.flatMap((r) => (r.job ? [r.job] : []));
  const followed = useQuery({
    queryKey: ["workload", "receive-follow", jobIds],
    queryFn: () => Promise.all(jobIds.map((id) => api<ReceiveJob>(`/api/core/jobs/${id}`))),
    enabled: i === 3 && jobIds.length > 0,
    refetchInterval: (q) => ((q.state.data ?? []).some((j) => !isSettled(j.status)) ? 3000 : false),
  });
  const retry = useMutation({
    mutationFn: (id: string) => api(`/api/core/jobs/${id}/steps/receive/retry`, { method: "POST" }),
    onSuccess: () => { toast.success("Đã yêu cầu nhập kho lại."); void followed.refetch(); },
  });
  const allocation = useQuery({
    queryKey: ["lifecycle", "allocation"],
    queryFn: () => api<Allocation>("/api/lifecycle/allocation"),
    enabled: i === 3 && can("lifecycle.job.view"),
    refetchInterval: 15000,
  });

  const receive = useMutation({
    mutationFn: async () => {
      const out: Result[] = [];
      for (const p of items) {
        const name = p.catalog_item?.name ?? p.item;
        try {
          const got = await api<Purchase>(`/api/workload/purchases/${p.id}/receive`, { method: "POST", json: { units: p.sdp_product ? unitsOf(p).map((u) => ({ serial: u.serial.trim(), asset_tag: u.asset_tag.trim() })) : [] } });
          out.push({ id: p.id, name, ok: true, job: got.receive_job?.id, received: got.status === "received" });
        } catch (e) {
          out.push({ id: p.id, name, ok: false, message: errorMessage(e) });
        }
      }
      return out;
    },
    onSuccess: (out) => {
      setResults(out);
      setI(3);
      void qc.invalidateQueries({ queryKey: ["workload"] });
    },
    meta: { silentError: true },
  });

  const setUnit = (p: Purchase, k: number, patch: Partial<Unit>) =>
    setUnits((cur) => ({ ...cur, [p.id]: unitsOf(p).map((u, j) => (j === k ? { ...u, ...patch } : u)) }));
  // Serial typed or scanned: keep the asset tag in step unless the person wrote their own.
  const setSerial = (p: Purchase, k: number, serial: string) => {
    const u = unitsOf(p)[k]!;
    const keep = u.asset_tag && !u.auto;
    setUnit(p, k, keep ? { serial } : { serial, asset_tag: tagFor(p, serial), auto: true });
  };
  const toConfirm = () => {
    setTried(true);
    if (!missing && !dupes.size && !tagDupes.size) setI(2);
  };
  const nextField = (id: string) => (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    const next = fieldIds[fieldIds.indexOf(id) + 1];
    if (next) document.getElementById(next)?.focus();
    else toConfirm(); // last scan: move on
  };
  const readFile = useMutation({
    mutationFn: (file: File) => {
      const fd = new FormData();
      fd.append("file", file);
      return api<{ rows: HandoverRow[] }>("/api/workload/purchases/receive/extract", { method: "POST", body: fd });
    },
    onSuccess: ({ rows }, file) => {
      const slots = tracked.flatMap((p) => unitsOf(p).map((u, k) => ({ key: `${p.id}:${k}`, name: p.catalog_item?.name ?? p.item, filled: !!u.serial.trim() })));
      const got = assignSerials(slots, rows);
      setUnits((cur) => {
        const next = { ...cur };
        for (const p of tracked) {
          next[p.id] = unitsOf(p).map((u, k) => {
            const r = got.get(`${p.id}:${k}`);
            if (!r) return u;
            return r.asset_tag ? { serial: r.serial, asset_tag: r.asset_tag, auto: false } : { serial: r.serial, asset_tag: tagFor(p, r.serial), auto: true };
          });
        }
        return next;
      });
      const empty = slots.filter((x) => !x.filled).length;
      const text = got.size
        ? `Đã điền ${got.size}/${empty} serial từ “${file.name}” (file có ${rows.length} dòng) – kiểm tra lại từng ô trước khi tiếp tục.`
        : `“${file.name}” có ${rows.length} serial nhưng không còn ô trống để điền.`;
      setFileNote({ ok: true, text });
      announce(text);
    },
    onError: (e) => {
      setFileNote({ ok: false, text: errorMessage(e) });
      announce(errorMessage(e));
    },
  });

  const header = (
    <>
      <nav aria-label="Đường dẫn">
        <Link to="/workload/purchases?tab=pos" className="mb-1 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
          <ChevronLeft className="size-4" aria-hidden="true" />PO tổng
        </Link>
      </nav>
      <PageHeader title="Nhận hàng theo PO" description="Quét serial từng thiết bị; hệ thống tạo tài sản In Store trên ServiceDesk Plus và cập nhật Kho thiết bị." />
    </>
  );
  if (pos.isLoading) return <>{header}<LoadingRows rows={5} /></>;

  if (i === 0) {
    return (
      <>
        {header}
        <Wizard
          label="Nhận hàng"
          steps={STEPS}
          current={0}
          title="Hàng của PO nào vừa về?"
          description="Chỉ hiện PO đã chốt còn hạng mục chưa nhận."
          next={{ label: "Tiếp tục", onClick: () => { setPoId(po!.id); setI(1); }, disabled: !po, reason: po ? null : open.length ? "Chọn một PO." : null }}
        >
          {noSdp && (
            <p role="status" className="mb-3 flex items-start gap-2 rounded-sm border border-waiting/40 bg-waiting-soft px-3 py-2 text-sm">
              <TriangleAlert className="mt-0.5 size-4 shrink-0 text-waiting" aria-hidden="true" />
              <span>
                {noSdpText} Vẫn quét serial được (được giữ lại khi tải lại trang), nhưng chỉ nhập kho được khi Quản trị{" "}
                {can("core.connections.manage") ? <Link className="font-medium text-primary underline" to="/settings/connections">bật kết nối SDP</Link> : "bật kết nối SDP"}.
              </span>
            </p>
          )}
          {open.length === 0 ? (
            <EmptyState icon={PackageCheck} title="Không có PO nào chờ nhận hàng">PO đã chốt còn hạng mục “Đã đặt mua” sẽ hiện ở đây.</EmptyState>
          ) : (
            <fieldset>
              <legend className="sr-only">PO chờ nhận hàng</legend>
              <ul className="space-y-2">
                {open.map((x) => (
                  <li key={x.id}>
                    <label className="flex cursor-pointer items-start gap-3 rounded-sm border border-line p-3 hover:bg-surface-2 has-[:checked]:border-primary has-[:checked]:bg-primary-soft">
                      <input type="radio" name="po" className="mt-1 size-4 accent-primary" checked={po?.id === x.id} onChange={() => setPoId(x.id)} />
                      <span className="min-w-0">
                        <span className="block font-medium"><Mono>{x.number}</Mono> · chốt {formatDate(x.closed_at)}</span>
                        <span className="block text-sm text-muted">{waitingItems(x).map((p) => `${p.catalog_item?.name ?? p.item} × ${p.quantity}`).join(" · ")}</span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            </fieldset>
          )}
        </Wizard>
      </>
    );
  }

  if (i === 1 && po) {
    return (
      <>
        {header}
        <Wizard
          label="Nhận hàng"
          steps={STEPS}
          current={1}
          title={`Serial thiết bị của ${po.number}`}
          description="Quét mã vạch vào từng ô (Enter sang ô kế tiếp) hoặc đọc từ file biên bản bàn giao. Mã tài sản được gợi ý theo serial."
          onBack={() => setI(0)}
          next={{
            label: "Tiếp tục",
            onClick: toConfirm,
            reason: tried && (missing || dupes.size || tagDupes.size) ? (dupes.size ? "Có serial bị trùng." : tagDupes.size ? "Có mã tài sản bị trùng." : `Còn ${missing} serial chưa nhập.`) : null,
          }}
        >
          {tracked.length > 0 && (
            <div className="mb-5 rounded-sm border border-dashed border-line px-3 py-3">
              <div className="flex flex-wrap items-center gap-2">
                <Button onClick={() => fileInput.current?.click()} loading={readFile.isPending}>
                  <FileSpreadsheet className="size-4" aria-hidden="true" />Đọc serial từ file biên bản
                </Button>
                <span className="text-sm text-muted">
                  PDF hoặc Excel (.xlsx) của nhà cung cấp
                  <HelpTip title="Đọc serial từ file">
                    <span className="block">Excel: cần một cột tiêu đề “Serial” / “S/N” / “Số serial” (cột “Asset tag” / “Mã tài sản” nếu có cũng được đọc).</span>
                    <span className="block">PDF: cần có chữ (không phải bản scan) và nhãn “S/N:” hoặc bảng có cột Serial.</span>
                    <span className="block">Serial được điền vào ô còn trống, khớp theo tên thiết bị; file không được lưu lại.</span>
                  </HelpTip>
                </span>
              </div>
              <input
                ref={fileInput}
                type="file"
                className="sr-only"
                tabIndex={-1}
                aria-hidden="true"
                accept=".pdf,.xlsx,application/pdf,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  e.target.value = "";
                  if (f) readFile.mutate(f);
                }}
              />
              {fileNote && <p className={fileNote.ok ? "mt-2 text-sm text-done" : "mt-2 text-sm text-failed"}>{fileNote.text}</p>}
            </div>
          )}
          <div className="space-y-5">
            {items.map((p) => (
              <section key={p.id} aria-label={p.catalog_item?.name ?? p.item}>
                <h3 className="mb-2 font-semibold">{p.catalog_item?.name ?? p.item} × {p.quantity} <span className="font-normal text-muted">· {p.employee || "—"}</span></h3>
                {p.sdp_product ? (
                  <ol className="space-y-2">
                    {unitsOf(p).map((u, k) => {
                      const id = `rcv-${p.id}-${k}`;
                      const key = u.serial.trim().toLowerCase();
                      const err = !key ? "Nhập serial." : key.length < 2 ? "Serial quá ngắn." : dupes.has(key) ? "Serial bị trùng." : "";
                      const tagErr = tagDupes.has(u.asset_tag.trim().toLowerCase()) ? "Mã tài sản bị trùng – sửa 4 ký tự cuối." : "";
                      return (
                        <li key={k} className="grid items-start gap-2 sm:grid-cols-[2rem_1fr_1fr]">
                          <span className="pt-2 text-sm text-muted tabular" aria-hidden="true">{k + 1}.</span>
                          <Field label={`Serial máy ${k + 1}`} required error={err} showError={tried && !!err}>
                            <Input id={id} data-autofocus={id === fieldIds[0] || undefined} value={u.serial} maxLength={100} onKeyDown={nextField(id)} onChange={(e) => setSerial(p, k, e.target.value)} />
                          </Field>
                          <Field label={`Mã tài sản máy ${k + 1}`} hint={u.auto && u.asset_tag ? "Gợi ý theo serial" : undefined} error={tagErr} showError={!!tagErr}>
                            <Input value={u.asset_tag} maxLength={100} onChange={(e) => setUnit(p, k, { asset_tag: e.target.value, auto: false })} />
                          </Field>
                        </li>
                      );
                    })}
                  </ol>
                ) : (
                  <p className="text-sm text-muted">Linh kiện / sửa chữa – không tạo tài sản trên SDP, chỉ đánh dấu đã nhận.</p>
                )}
              </section>
            ))}
          </div>
        </Wizard>
      </>
    );
  }

  if (i === 2 && po) {
    const assets = tracked.reduce((n, p) => n + p.quantity, 0);
    return (
      <>
        {header}
        <Wizard
          label="Nhận hàng"
          steps={STEPS}
          current={2}
          title="Xác nhận nhập kho"
          description="Đây là bước ghi lên ServiceDesk Plus."
          onBack={() => setI(1)}
          next={{
            label: <><PackageCheck className="size-4" aria-hidden="true" />Nhập kho {items.length} hạng mục</>,
            onClick: () => receive.mutate(),
            loading: receive.isPending,
            disabled: assets > 0 && !conn?.enabled,
            reason: assets > 0 && !conn?.enabled ? `${noSdpText} Serial đã nhập được giữ lại.` : null,
          }}
        >
          <ul className="list-disc space-y-1.5 pl-5 text-sm">
            {assets > 0 && (
              <li>
                Tạo <b>{assets} tài sản</b> trạng thái “{target.data?.state}” trên ServiceDesk Plus
                {conn && <> – kết nối <b>{conn.name}</b> ({conn.environment === "prod" ? "Production" : "Test"})</>}. Serial đã có trên SDP được giữ nguyên, không tạo trùng.
              </li>
            )}
            {items.length > tracked.length && <li>Đánh dấu đã nhận {items.length - tracked.length} hạng mục linh kiện/sửa chữa.</li>}
            <li>Tài sản đã tạo <b>không tự xoá được</b> – nhập sai thì sửa trực tiếp trên ServiceDesk Plus.</li>
            <li>Xong, Kho thiết bị được đồng bộ lại để gợi ý thiết bị mới cho nhân viên sắp onboard.</li>
          </ul>
        </Wizard>
      </>
    );
  }

  // Results + what can now be allocated.
  const jobs = followed.data ?? [];
  const needs = (allocation.data?.requests ?? []).filter((r) => r.status === "buy");
  return (
    <>
      {header}
      <Wizard label="Nhận hàng" steps={STEPS} current={3} title="Kết quả nhập kho" description="Trạng thái cập nhật tự động.">
        <ul className="divide-y divide-line rounded-sm border border-line text-sm">
          {results.map((r) => {
            const j = jobs.find((x) => x.id === r.job);
            const step = j?.steps[0];
            const n = step?.output.assets?.length ?? 0;
            const failed = !r.ok || j?.status === "failed";
            const done = r.received || j?.status === "completed";
            return (
              <li key={r.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                {done ? <CheckCircle2 className="size-4 text-done" aria-hidden="true" /> : failed ? <TriangleAlert className="size-4 text-failed" aria-hidden="true" /> : <LoaderCircle className="size-4 text-progress motion-safe:animate-spin" aria-hidden="true" />}
                <span className="min-w-0 flex-1 font-medium">{r.name}</span>
                <span className={done ? "text-done" : failed ? "text-failed" : "text-progress"}>
                  {done ? `Đã nhập kho${n ? ` (${n} tài sản)` : ""}` : failed ? `Lỗi: ${r.message ?? step?.message ?? ""}` : "Đang tạo tài sản trên SDP…"}
                </span>
                {j?.status === "failed" && (
                  <Button size="sm" loading={retry.isPending && retry.variables === j.id} onClick={() => retry.mutate(j.id)}>Thử lại</Button>
                )}
              </li>
            );
          })}
        </ul>
        {can("lifecycle.job.view") && (
          <section className="mt-5" aria-labelledby="alloc-title">
            <h3 id="alloc-title" className="mb-1 font-semibold">Nhân viên sắp onboard còn thiếu thiết bị</h3>
            <p className="mb-2 text-sm text-muted">Kho cập nhật sau lần đồng bộ kế tiếp{allocation.dataUpdatedAt ? ` (kiểm tra lúc ${formatDateTime(new Date(allocation.dataUpdatedAt))})` : ""}; thiết bị vừa nhận sẽ được gợi ý tận dụng trong Job.</p>
            {needs.length === 0 ? (
              <p className="text-sm text-done">Không còn onboard nào thiếu thiết bị.</p>
            ) : (
              <ul className="divide-y divide-line rounded-sm border border-line text-sm">
                {needs.map((r) => (
                  <li key={r.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                    <span className="min-w-0 flex-1">{r.full_name} · hiệu lực {formatDate(r.effective_date)} · thiếu {r.lines.filter((l) => l.buy).map((l) => `${l.label} × ${l.buy}`).join(", ")}</span>
                    {r.job && <Link className="font-medium text-primary hover:underline" to={`/lifecycle/jobs/${r.job.id}?view=wizard`}>Mở Job</Link>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
        <div className="mt-5 flex flex-wrap justify-end gap-2">
          <Button onClick={() => navigate("/me/work")}>Việc tiếp theo</Button>
          <Button variant="primary" onClick={() => { setResults([]); setUnits({}); setTried(false); setPoId(""); setFileNote(null); setI(0); }}>Nhận PO khác</Button>
        </div>
      </Wizard>
    </>
  );
}
