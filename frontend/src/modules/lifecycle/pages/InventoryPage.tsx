import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Boxes, CheckCircle2, PackageOpen, PackageSearch, Pencil, ShoppingCart, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { cn } from "@/core/lib/cn";
import { formatDate } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, Mono, Panel } from "@/core/ui/feedback";
import { Field, Input } from "@/core/ui/field";
import { buyLink as buyLinkFor, proposedFor } from "../buy";
import { StatusBadge, type Status } from "@/core/ui/status";

interface Asset {
  id: string;
  name: string | null;
  asset_tag: string | null;
  product: string | null;
  product_type: string | null;
  returned?: boolean;
}
interface Line {
  label: string;
  quantity: number;
  catalog_name: string;
  picked: Asset[];
  reuse: Asset[];
  buy: number;
}
export interface PlanRequest {
  id: string;
  display_id: string | null;
  full_name: string | null;
  employee_id: string | null;
  department: string | null;
  job_title: string | null;
  effective_date: string | null;
  band: string | null;
  band_source: "rtr" | "override" | null;
  job: { id: string; status: string; version: number } | null;
  status: "no_band" | "buy" | "covered";
  lines: Line[];
}
export interface Allocation {
  as_of: string | null;
  stock: StockLine[];
  requests: PlanRequest[];
  bands: string[];
  no_band: number; // hires without a band: their need is not in to_buy
}
export interface PurchaseLite {
  source_ref: string | null;
  item: string;
  status: string;
}

const REQ_STATUS: Record<PlanRequest["status"], { status: Status; label: string }> = {
  covered: { status: "done", label: "Đủ thiết bị" },
  buy: { status: "warning", label: "Thiếu thiết bị" },
  no_band: { status: "waiting", label: "Chưa có band" },
};

interface StockItem extends Asset {
  serial: string | null;
  reserved_for: { name: string | null; display_id: string; chosen: boolean } | null;
}
export interface StockLine {
  label: string;
  bands: string[];
  product_types: string[];
  in_store: number;
  planned: number; // in stock and allocated to an upcoming hire (chosen on a job + suggested)
  chosen: number;
  to_buy: number;
  items: StockItem[];
  standard: boolean; // false: In Store device type outside the allocation standard (PC, UPS, …)
}

const assetText = (a: Asset) => `${a.name ?? a.id}${a.asset_tag ? ` (${a.asset_tag})` : ""}`;
function BandDialog({ req, bands, onClose }: { req: PlanRequest; bands: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [band, setBand] = useState(req.band_source === "override" ? (req.band ?? "") : "");
  const save = useMutation({
    mutationFn: () => api(`/api/lifecycle/requests/${req.id}/band`, { method: "PUT", json: { band } }),
    onSuccess: () => {
      toast.success(band ? `Đã đặt band ${band} cho ${req.full_name}.` : "Đã dùng lại band trong request RTR.");
      void qc.invalidateQueries({ queryKey: ["lifecycle", "allocation"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog
      open
      size="sm"
      onClose={onClose}
      title={`Band của ${req.full_name ?? req.employee_id}`}
      description={req.band_source === "rtr" ? `Request RTR ghi band ${req.band}.` : "Request RTR không ghi band."}
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" loading={save.isPending} onClick={() => save.mutate()}>Lưu</Button>
        </>
      }
    >
      <Field label="Band" hint={`Để trống để dùng band trong request RTR.${bands.length ? ` Band có trong tiêu chuẩn: ${bands.join(", ")}.` : ""}`}>
        <Input data-autofocus value={band} maxLength={32} list="known-bands" onChange={(e) => setBand(e.target.value)} />
      </Field>
      <datalist id="known-bands">{bands.map((b) => <option key={b} value={b} />)}</datalist>
    </Dialog>
  );
}

export function RequestCard({ r, purchases, bands }: { r: PlanRequest; purchases: PurchaseLite[] | null; bands: string[] }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [editBand, setEditBand] = useState(false);
  const ref = r.display_id ?? r.id;
  const reuse = r.lines.flatMap((l) => l.reuse);
  const apply = useMutation({
    mutationFn: () =>
      api(`/api/lifecycle/jobs/${r.job!.id}/params`, {
        method: "PUT",
        json: {
          version: r.job!.version,
          assets: r.lines.flatMap((l) => [...l.picked, ...l.reuse]).map((a) => ({ id: a.id, name: a.name ?? a.id, tag: a.asset_tag, product_type: a.product_type })),
        },
      }),
    onSuccess: () => {
      toast.success(`Đã chọn ${reuse.length} tài sản từ kho vào Job của ${r.full_name}.`);
      void qc.invalidateQueries({ queryKey: ["lifecycle"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const proposed = (l: Line) => proposedFor(purchases, r, l);
  const buyLink = (l: Line) => buyLinkFor(r, l);

  return (
    <li className="rounded-md border border-line bg-surface p-4 shadow-card">
      <div className="flex flex-col gap-1 sm:flex-row sm:flex-wrap sm:items-start sm:gap-x-3">
        <div className="min-w-0 sm:flex-1">
          <p className="font-semibold">{r.full_name} <span className="font-normal text-muted">({r.employee_id})</span></p>
          <p className="text-sm text-muted">
            Hiệu lực {formatDate(r.effective_date)} · {r.department ?? "—"}{r.job_title ? ` · ${r.job_title}` : ""} · request <Mono>#{ref}</Mono>
          </p>
        </div>
        <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <span className="inline-flex items-center gap-1 text-sm">
          Band <b className={cn(!r.band && "text-waiting")}>{r.band ?? "chưa có"}</b>
          {r.band_source === "override" && <span className="text-xs text-muted">(nhập tay)</span>}
          {can("lifecycle.job.execute") && (
            <Button size="icon" variant="ghost" aria-label={`Sửa band của ${r.full_name}`} onClick={() => setEditBand(true)}>
              <Pencil className="size-4" aria-hidden="true" />
            </Button>
          )}
        </span>
        <StatusBadge {...REQ_STATUS[r.status]} />
        </span>
      </div>

      {r.status === "no_band" ? (
        <p className="mt-3 text-sm text-waiting">Request RTR không ghi band – bấm biểu tượng bút để nhập, hệ thống mới tính được thiết bị cần cấp.</p>
      ) : (
        <ul className="mt-3 divide-y divide-line text-sm">
          {r.lines.map((l) => {
            const po = proposed(l);
            return (
              <li key={l.label} className="flex flex-wrap items-center gap-x-3 gap-y-1.5 py-2">
                <span className="w-36 shrink-0 font-medium">{l.label} × {l.quantity}</span>
                <span className="flex min-w-0 flex-1 flex-wrap gap-1.5">
                  {l.picked.map((a) => (
                    <span key={a.id} className="inline-flex items-center gap-1 rounded-sm bg-done-soft px-1.5 py-0.5 text-done"><CheckCircle2 className="size-3.5" aria-hidden="true" />Đã chọn: {assetText(a)}</span>
                  ))}
                  {l.reuse.map((a) => (
                    <Link
                      key={a.id}
                      to={`/lifecycle/stock?tab=assets&q=${encodeURIComponent(a.asset_tag ?? a.name ?? a.id)}`}
                      className="inline-flex items-center gap-1 rounded-sm bg-primary-soft px-1.5 py-0.5 text-primary hover:underline"
                    >
                      <PackageOpen className="size-3.5" aria-hidden="true" />Dự kiến: {assetText(a)}{a.returned ? " · Thu hồi" : ""}
                    </Link>
                  ))}
                  {l.buy > 0 && (
                    <span className="inline-flex items-center gap-1 rounded-sm bg-waiting-soft px-1.5 py-0.5 text-waiting"><ShoppingCart className="size-3.5" aria-hidden="true" />Thiếu {l.buy}</span>
                  )}
                </span>
                {l.buy > 0 &&
                  (po ? (
                    <span className="text-xs text-muted">Đã đề xuất mua ({po.status === "proposed" ? "chờ duyệt" : "đã duyệt/đặt"})</span>
                  ) : (
                    can("workload.purchase.create") && (
                      <Link to={buyLink(l)} className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 font-medium hover:bg-surface-2">
                        <ShoppingCart className="size-4" aria-hidden="true" />Lập đề xuất mua {l.buy}
                      </Link>
                    )
                  ))}
              </li>
            );
          })}
        </ul>
      )}

      <div className="mt-2 flex flex-wrap gap-2">
        {r.job?.status === "draft" && reuse.length > 0 && can("lifecycle.job.execute") && (
          <Button size="sm" variant="primary" loading={apply.isPending} onClick={() => apply.mutate()}>
            <Wand2 className="size-4" aria-hidden="true" />Chọn {reuse.length} tài sản dự kiến vào Job
          </Button>
        )}
        {r.job && (
          <Link to={`/lifecycle/jobs/${r.job.id}`} className="inline-flex h-8 items-center rounded-sm px-2 text-sm font-medium text-primary hover:underline">Mở Job</Link>
        )}
      </div>
      {editBand && <BandDialog req={r} bands={bands} onClose={() => setEditBand(false)} />}
    </li>
  );
}

const isoToday = () => new Date().toLocaleDateString("sv-SE", { timeZone: "Asia/Ho_Chi_Minh" });
const isoIn = (days: number) => new Date(Date.now() + days * 86_400_000).toLocaleDateString("sv-SE", { timeZone: "Asia/Ho_Chi_Minh" });

/** People who need devices: most urgent first (start date passed, within 7 days, later). */
export function OnboardTab({ d, purchases }: { d: Allocation; purchases: PurchaseLite[] | null }) {
  if (d.requests.length === 0)
    return (
      <Panel>
        <EmptyState icon={d.as_of ? Boxes : PackageSearch} title="Không có nhân viên mới nào cần chuẩn bị thiết bị">
          Request Nhân viên mới chưa đóng sẽ hiện ở đây sau khi đồng bộ.
        </EmptyState>
      </Panel>
    );
  const today = isoToday();
  const week = isoIn(7);
  const byDate = [...d.requests].sort((x, y) => (x.effective_date ?? "9999").localeCompare(y.effective_date ?? "9999"));
  const groups = [
    { key: "late", title: "Đã qua ngày bắt đầu", items: byDate.filter((r) => r.effective_date && r.effective_date < today) },
    { key: "soon", title: "Bắt đầu trong 7 ngày", items: byDate.filter((r) => r.effective_date && r.effective_date >= today && r.effective_date <= week) },
    { key: "later", title: "Sau đó", items: byDate.filter((r) => !r.effective_date || r.effective_date > week) },
  ].filter((g) => g.items.length);
  return (
    <div className="space-y-5">
      {groups.map((g) => (
        <section key={g.key} aria-labelledby={`onboard-${g.key}`}>
          <h2 id={`onboard-${g.key}`} className={cn("mb-2 text-sm font-semibold", g.key === "late" ? "text-failed" : "text-muted")}>
            {g.title} ({g.items.length})
          </h2>
          <ul className="space-y-3">
            {g.items.map((r) => <RequestCard key={r.id} r={r} purchases={purchases} bands={d.bands} />)}
          </ul>
        </section>
      ))}
    </div>
  );
}
