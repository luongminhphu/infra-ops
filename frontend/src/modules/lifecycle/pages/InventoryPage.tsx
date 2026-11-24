import { useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Boxes, CheckCircle2, PackageSearch, Pencil, Recycle, ShoppingCart, Wand2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { cn } from "@/core/lib/cn";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { buyLink as buyLinkFor, proposedFor } from "../buy";
import { StatusBadge, type Status } from "@/core/ui/status";

interface Asset {
  id: string;
  name: string | null;
  asset_tag: string | null;
  product: string | null;
  product_type: string | null;
}
interface Line {
  label: string;
  quantity: number;
  catalog_name: string;
  picked: Asset[];
  reuse: Asset[];
  buy: number;
}
interface PlanRequest {
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
interface Allocation {
  as_of: string | null;
  stock: { label: string; bands: string[]; product_types: string[]; in_store: number; planned: number; to_buy: number }[];
  requests: PlanRequest[];
  bands: string[];
}
interface PurchaseLite {
  source_ref: string | null;
  item: string;
  status: string;
}

const REQ_STATUS: Record<PlanRequest["status"], { status: Status; label: string }> = {
  covered: { status: "done", label: "Đủ từ kho" },
  buy: { status: "warning", label: "Cần mua thêm" },
  no_band: { status: "waiting", label: "Thiếu band" },
};

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

function RequestCard({ r, purchases, bands }: { r: PlanRequest; purchases: PurchaseLite[] | null; bands: string[] }) {
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
      toast.success(`Đã chọn ${reuse.length} tài sản tận dụng vào Job của ${r.full_name}.`);
      void qc.invalidateQueries({ queryKey: ["lifecycle"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const proposed = (l: Line) => proposedFor(purchases, r, l);
  const buyLink = (l: Line) => buyLinkFor(r, l);

  return (
    <li className="rounded-md border border-line bg-surface p-4 shadow-card">
      <div className="flex flex-wrap items-start gap-x-3 gap-y-1">
        <div className="min-w-0 flex-1">
          <p className="font-semibold">{r.full_name} <span className="font-normal text-muted">({r.employee_id})</span></p>
          <p className="text-sm text-muted">
            Hiệu lực {formatDate(r.effective_date)} · {r.department ?? "—"}{r.job_title ? ` · ${r.job_title}` : ""} · request <Mono>#{ref}</Mono>
          </p>
        </div>
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
      </div>

      {r.status === "no_band" ? (
        <p className="mt-3 text-sm text-waiting">Request RTR không ghi band – bấm biểu tượng bút để nhập band, hệ thống mới đối chiếu được tiêu chuẩn cấp phát.</p>
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
                    <span key={a.id} className="inline-flex items-center gap-1 rounded-sm bg-primary-soft px-1.5 py-0.5 text-primary"><Recycle className="size-3.5" aria-hidden="true" />Tận dụng: {assetText(a)}</span>
                  ))}
                  {l.buy > 0 && (
                    <span className="inline-flex items-center gap-1 rounded-sm bg-waiting-soft px-1.5 py-0.5 text-waiting"><ShoppingCart className="size-3.5" aria-hidden="true" />Cần mua {l.buy}</span>
                  )}
                </span>
                {l.buy > 0 &&
                  (po ? (
                    <span className="text-xs text-muted">Đã đề xuất mua ({po.status === "proposed" ? "chờ duyệt" : "đã duyệt/đặt"})</span>
                  ) : (
                    can("workload.purchase.create") && (
                      <Link to={buyLink(l)} className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 font-medium hover:bg-surface-2">
                        <ShoppingCart className="size-4" aria-hidden="true" />Lập đề xuất mua
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
            <Wand2 className="size-4" aria-hidden="true" />Chọn {reuse.length} tài sản tận dụng vào Job
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

export function InventoryPage() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["lifecycle", "allocation"], queryFn: () => api<Allocation>("/api/lifecycle/allocation") });
  const purchases = useQuery({
    queryKey: ["workload", "purchases", "onboard"],
    queryFn: () => api<{ items: PurchaseLite[] }>("/api/workload/purchases"),
    enabled: can("workload.tickets.view"),
  });
  const d = q.data;
  return (
    <>
      <PageHeader
        title="Kho & cấp phát"
        description="Đối chiếu band của nhân viên sắp onboard với tiêu chuẩn cấp phát và tài sản In Store trên ServiceDesk Plus: tận dụng thiết bị trong kho trước, thiếu mới đề xuất mua."
      />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading || !d ? (
        !q.isError && <LoadingRows rows={6} />
      ) : (
        // Phone: employees to act on come first, the stock summary after them.
        <div className="flex flex-col">
          <p className="mb-3 flex flex-wrap items-center gap-1 text-sm text-muted">
            {d.as_of ? `Theo dữ liệu kho đồng bộ lúc ${formatDateTime(d.as_of)}.` : "Chưa đồng bộ dữ liệu kho – vào Đồng bộ dữ liệu để chạy lần đầu."}
            <HelpTip title="Cách hệ thống gợi ý">
              <span className="block">1. Lấy <b>band</b> trong request RTR (hoặc band nhập tay) và tra <b>tiêu chuẩn cấp phát</b> (Cấu hình module).</span>
              <span className="block">2. Mỗi dòng tiêu chuẩn tìm tài sản <b>In Store, chưa gán user</b> đúng loại; ai có ngày hiệu lực sớm hơn được ưu tiên. Tài sản đã chọn trên Job nháp được giữ cho Job đó.</span>
              <span className="block">3. Không còn tài sản phù hợp → <b>Cần mua</b> → bấm <b>Lập đề xuất mua</b>, đính kèm bằng chứng Function Head duyệt.</span>
              <span className="block">Dữ liệu kho theo lần đồng bộ gần nhất (menu Đồng bộ dữ liệu).</span>
            </HelpTip>
          </p>
          <section aria-label="Tồn kho theo tiêu chuẩn" className="order-last mt-5 grid gap-3 sm:order-none sm:mt-0 sm:mb-5 sm:grid-cols-2 lg:grid-cols-4">
            {d.stock.map((s) => (
              <Panel key={s.label} title={s.label} description={s.bands.length ? `Band ${s.bands.join(", ")}` : undefined} bodyClassName="grid grid-cols-3 gap-2 p-3 text-center">
                <div><p className="text-xl font-semibold tabular">{s.in_store}</p><p className="text-xs text-muted">Trong kho</p></div>
                <div><p className="text-xl font-semibold tabular">{s.planned}</p><p className="text-xs text-muted">Sẽ dùng</p></div>
                <div><p className={cn("text-xl font-semibold tabular", s.to_buy > 0 && "text-waiting")}>{s.to_buy}</p><p className="text-xs text-muted">Cần mua</p></div>
              </Panel>
            ))}
          </section>
          <h2 className="mb-2 flex items-center gap-1 text-lg font-semibold">
            Nhân viên sắp onboard ({d.requests.length})
          </h2>
          {d.requests.length === 0 ? (
            <Panel><EmptyState icon={d.as_of ? Boxes : PackageSearch} title="Không có onboard nào cần chuẩn bị thiết bị">Request Nhân viên mới chưa đóng sẽ hiện ở đây sau khi đồng bộ.</EmptyState></Panel>
          ) : (
            <ul className="space-y-3">
              {d.requests.map((r) => <RequestCard key={r.id} r={r} purchases={purchases.data?.items ?? null} bands={d.bands} />)}
            </ul>
          )}
        </div>
      )}
    </>
  );
}
