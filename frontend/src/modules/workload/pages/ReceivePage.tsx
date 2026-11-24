import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronLeft, LoaderCircle, PackageCheck, TriangleAlert } from "lucide-react";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { EmptyState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input } from "@/core/ui/field";
import { Wizard } from "@/core/ui/wizard";
import type { Purchase, PurchaseOrder } from "../api";

const STEPS = [
  { key: "po", title: "Chọn PO" },
  { key: "scan", title: "Serial từng máy" },
  { key: "confirm", title: "Xác nhận nhập kho" },
  { key: "done", title: "Kết quả" },
];

interface Unit {
  serial: string;
  asset_tag: string;
}
interface Target {
  connection: { name: string; environment: string; enabled: boolean } | null;
  state: string;
}
interface Allocation {
  requests: { id: string; display_id: string | null; full_name: string | null; effective_date: string | null; status: string; job: { id: string } | null; lines: { label: string; buy: number }[] }[];
}

const waitingItems = (po: PurchaseOrder) => po.items.filter((p) => p.status === "ordered" && !p.receive_job);

/**
 * Supervisor: receive the goods of a closed PO in one pass – pick the PO, scan a serial per device
 * (Enter moves to the next field), confirm the SDP write, then see what onboards can now be equipped.
 */
export function ReceivePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const { can } = useAuth();
  const [i, setI] = useState(0);
  const [poId, setPoId] = useState(params.get("po") ?? "");
  const [units, setUnits] = useState<Record<string, Unit[]>>({});
  const [tried, setTried] = useState(false);
  const [results, setResults] = useState<{ id: string; ok: boolean; message?: string }[]>([]);
  const pos = useQuery({ queryKey: ["workload", "pos"], queryFn: () => api<{ closed: PurchaseOrder[] }>("/api/workload/pos") });
  const target = useQuery({ queryKey: ["workload", "receive-target"], queryFn: () => api<Target>("/api/workload/purchases/receive-target") });
  const open = (pos.data?.closed ?? []).filter((po) => waitingItems(po).length > 0);
  const po = open.find((x) => x.id === poId) ?? (open.length === 1 ? open[0] : undefined);
  const items = po ? waitingItems(po) : [];
  const tracked = items.filter((p) => p.sdp_product);
  const unitsOf = (p: Purchase) => units[p.id] ?? Array.from({ length: p.quantity }, () => ({ serial: "", asset_tag: "" }));
  const allSerials = tracked.flatMap((p) => unitsOf(p).map((u) => u.serial.trim().toLowerCase()));
  const dupes = useMemo(() => new Set(allSerials.filter((s, k) => s && allSerials.indexOf(s) !== k)), [allSerials]);
  const missing = allSerials.filter((s) => s.length < 2).length;
  const fieldIds = tracked.flatMap((p) => unitsOf(p).map((_, k) => `rcv-${p.id}-${k}`));

  // Results step: follow the SDP jobs until each proposal is received or failed.
  const followed = useQuery({
    queryKey: ["workload", "purchases", "receive-follow", poId],
    queryFn: () => api<{ items: Purchase[] }>("/api/workload/purchases?status=").then((r) => r.items.filter((p) => results.some((x) => x.id === p.id))),
    enabled: i === 3 && results.length > 0,
    refetchInterval: (q) => ((q.state.data ?? []).some((p) => p.status === "ordered" && p.receive_job?.status !== "failed") ? 3000 : false),
  });
  const allocation = useQuery({
    queryKey: ["lifecycle", "allocation"],
    queryFn: () => api<Allocation>("/api/lifecycle/allocation"),
    enabled: i === 3 && can("lifecycle.job.view"),
    refetchInterval: 15000,
  });

  const receive = useMutation({
    mutationFn: async () => {
      const out: { id: string; ok: boolean; message?: string }[] = [];
      for (const p of items) {
        try {
          await api(`/api/workload/purchases/${p.id}/receive`, { method: "POST", json: { units: p.sdp_product ? unitsOf(p).map((u) => ({ serial: u.serial.trim(), asset_tag: u.asset_tag.trim() })) : [] } });
          out.push({ id: p.id, ok: true });
        } catch (e) {
          out.push({ id: p.id, ok: false, message: errorMessage(e) });
        }
      }
      return out;
    },
    onSuccess: (out) => {
      setResults(out);
      setI(3);
      void qc.invalidateQueries({ queryKey: ["workload"] });
    },
  });

  const setUnit = (p: Purchase, k: number, patch: Partial<Unit>) =>
    setUnits((cur) => ({ ...cur, [p.id]: unitsOf(p).map((u, j) => (j === k ? { ...u, ...patch } : u)) }));
  const nextField = (id: string) => (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key !== "Enter") return;
    e.preventDefault();
    document.getElementById(fieldIds[fieldIds.indexOf(id) + 1] ?? "")?.focus();
  };

  const header = (
    <>
      <nav aria-label="Đường dẫn">
        <Link to="/workload/purchases?tab=pos" className="mb-1 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
          <ChevronLeft className="size-4" aria-hidden="true" />PO tổng
        </Link>
      </nav>
      <PageHeader title="Nhận hàng theo PO" description="Quét serial từng thiết bị; hệ thống tạo tài sản In Store trên ServiceDesk Plus và cập nhật Kho & cấp phát." />
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
          description="Quét mã vạch vào từng ô – Enter chuyển sang ô kế tiếp. Asset tag (nếu có) dùng làm tên tài sản."
          onBack={() => setI(0)}
          next={{
            label: "Tiếp tục",
            onClick: () => { setTried(true); if (!missing && !dupes.size) setI(2); },
            reason: tried && (missing || dupes.size) ? (dupes.size ? "Có serial bị trùng." : `Còn ${missing} serial chưa nhập.`) : null,
          }}
        >
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
                      return (
                        <li key={k} className="grid items-start gap-2 sm:grid-cols-[2rem_1fr_1fr]">
                          <span className="pt-2 text-sm text-muted tabular" aria-hidden="true">{k + 1}.</span>
                          <Field label={`Serial máy ${k + 1}`} required error={err} showError={tried && !!err}>
                            <Input id={id} data-autofocus={id === fieldIds[0]} value={u.serial} maxLength={100} onKeyDown={nextField(id)} onChange={(e) => setUnit(p, k, { serial: e.target.value })} />
                          </Field>
                          <Field label={`Asset tag máy ${k + 1}`}>
                            <Input value={u.asset_tag} maxLength={100} onChange={(e) => setUnit(p, k, { asset_tag: e.target.value })} />
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
    const conn = target.data?.connection;
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
            reason: assets > 0 && !conn ? "Chưa có kết nối ServiceDesk Plus." : assets > 0 && conn && !conn.enabled ? "Kết nối SDP đang tắt." : null,
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
            <li>Xong, Kho & cấp phát được đồng bộ lại để gợi ý thiết bị mới cho nhân viên sắp onboard.</li>
          </ul>
        </Wizard>
      </>
    );
  }

  // Results + what can now be allocated.
  const rows = followed.data ?? [];
  const needs = (allocation.data?.requests ?? []).filter((r) => r.status === "buy");
  return (
    <>
      {header}
      <Wizard label="Nhận hàng" steps={STEPS} current={3} title="Kết quả nhập kho" description="Trạng thái cập nhật tự động.">
        <ul className="divide-y divide-line rounded-sm border border-line text-sm">
          {results.map((r) => {
            const p = rows.find((x) => x.id === r.id) ?? items.find((x) => x.id === r.id);
            const failed = !r.ok || p?.receive_job?.status === "failed";
            const done = p?.status === "received";
            return (
              <li key={r.id} className="flex flex-wrap items-center gap-2 px-3 py-2">
                {done ? <CheckCircle2 className="size-4 text-done" aria-hidden="true" /> : failed ? <TriangleAlert className="size-4 text-failed" aria-hidden="true" /> : <LoaderCircle className="size-4 text-progress motion-safe:animate-spin" aria-hidden="true" />}
                <span className="min-w-0 flex-1 font-medium">{p?.catalog_item?.name ?? p?.item}</span>
                <span className={done ? "text-done" : failed ? "text-failed" : "text-progress"}>
                  {done ? `Đã nhập kho${p?.received_assets.length ? ` (${p.received_assets.length} tài sản)` : ""}` : failed ? `Lỗi: ${r.message ?? p?.receive_job?.error ?? ""} – thử lại ở danh sách đề xuất` : "Đang tạo tài sản trên SDP…"}
                </span>
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
          <Button variant="primary" onClick={() => { setResults([]); setUnits({}); setTried(false); setPoId(""); setI(0); }}>Nhận PO khác</Button>
        </div>
      </Wizard>
    </>
  );
}
