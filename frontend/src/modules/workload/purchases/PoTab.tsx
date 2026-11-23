import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Lock, Mail, PackageCheck } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { cn } from "@/core/lib/cn";
import { formatDateTime, formatVnd } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono, Panel } from "@/core/ui/feedback";
import { HelpTip } from "@/core/ui/help-tip";
import { announce } from "@/core/ui/live-region";
import type { PurchaseOrder } from "../api";
import { sourceLabel } from "./ProposalsTab";

interface PosResponse {
  limit: number;
  open: PurchaseOrder[];
  closed: PurchaseOrder[];
  proposed_waiting: number;
}

function downloadEmail(po: { id: string }) {
  const a = document.createElement("a");
  a.href = `/api/workload/pos/${po.id}/email`;
  a.download = "";
  document.body.append(a);
  a.click();
  a.remove();
}

function Usage({ po }: { po: PurchaseOrder }) {
  const pct = Math.round((po.total / po.limit) * 100);
  return (
    <div>
      <div className="mb-1 flex flex-wrap items-baseline justify-between gap-2 text-sm">
        <span className="tabular font-semibold">{formatVnd(po.total)}</span>
        <span className="tabular text-muted">/ hạn mức {formatVnd(po.limit)} · {pct}%</span>
      </div>
      <div role="meter" aria-label="Giá trị PO so với hạn mức" aria-valuemin={0} aria-valuemax={po.limit} aria-valuenow={po.total} aria-valuetext={`${pct}% hạn mức`} className="h-2 overflow-hidden rounded-full bg-surface-2">
        <div className={cn("h-full rounded-full", po.over_limit ? "bg-failed" : pct >= 90 ? "bg-waiting" : "bg-primary")} style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
    </div>
  );
}

function Items({ po }: { po: PurchaseOrder }) {
  return (
    <table className="mt-3 w-full text-sm">
      <caption className="sr-only">Hạng mục của PO</caption>
      <thead>
        <tr className="border-b border-line text-left text-xs text-muted">
          <th scope="col" className="py-1.5 pr-2 font-medium">Thiết bị</th>
          <th scope="col" className="py-1.5 pr-2 text-right font-medium">SL</th>
          <th scope="col" className="py-1.5 pr-2 text-right font-medium">Thành tiền</th>
          <th scope="col" className="hidden py-1.5 font-medium sm:table-cell">Nguồn · người dùng</th>
        </tr>
      </thead>
      <tbody>
        {po.items.map((p) => (
          <tr key={p.id} className="border-b border-line last:border-0">
            <td className="py-1.5 pr-2">{p.catalog_item?.name ?? p.item}</td>
            <td className="py-1.5 pr-2 text-right tabular">{p.quantity}</td>
            <td className="py-1.5 pr-2 text-right tabular whitespace-nowrap">{formatVnd(p.amount)}</td>
            <td className="hidden py-1.5 text-muted sm:table-cell"><Mono>{sourceLabel(p)}</Mono>{p.employee ? ` · ${p.employee}` : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function PoTab() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const canClose = can("workload.po.close");
  const [closing, setClosing] = useState<PurchaseOrder | null>(null);
  const q = useQuery({ queryKey: ["workload", "pos"], queryFn: () => api<PosResponse>("/api/workload/pos") });
  const close = useMutation({
    mutationFn: (po: PurchaseOrder) => api<PurchaseOrder>(`/api/workload/pos/${po.id}/close`, { method: "POST", json: { version_total: po.total } }),
    onSuccess: (po) => {
      setClosing(null);
      const msg = `Đã chốt ${po.number} – ${formatVnd(po.total)}. Đang tải email nháp, mở file .eml bằng Outlook để kiểm tra và gửi.`;
      toast.success(msg);
      announce(msg);
      downloadEmail(po);
      void qc.invalidateQueries({ queryKey: ["workload"] });
    },
    onError: (e) => {
      toast.error(errorMessage(e));
      void qc.invalidateQueries({ queryKey: ["workload", "pos"] });
    },
  });

  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (q.isLoading || !q.data) return <LoadingRows rows={5} />;
  const d = q.data;
  return (
    <div className="space-y-4">
      <p className="flex flex-wrap items-center gap-1 text-sm text-muted">
        Hạn mức mỗi PO: <b className="tabular text-ink">{formatVnd(d.limit)}</b>
        {d.proposed_waiting > 0 && <> · {d.proposed_waiting} đề xuất đang chờ duyệt (chưa vào PO)</>}
        <HelpTip title="PO tổng được gom thế nào?">
          <span className="block">Mỗi đề xuất <b>đã duyệt</b> tự vào PO nháp đầu tiên còn đủ chỗ, theo thứ tự thời gian duyệt. Không còn PO nào đủ chỗ thì hệ thống <b>tự mở PO tiếp theo</b>.</span>
          <span className="block">Đổi đơn giá trong Bảng giá hoặc đổi hạn mức thì các PO nháp được gom lại; PO đã chốt không thay đổi.</span>
          <span className="block">Một đề xuất đơn lẻ vượt hạn mức nằm riêng một PO và được đánh dấu đỏ.</span>
        </HelpTip>
      </p>

      {d.open.length === 0 ? (
        <Panel title="PO nháp">
          <EmptyState icon={PackageCheck} title="Chưa có PO nháp">Duyệt đề xuất ở thẻ Đề xuất để hệ thống gom thành PO.</EmptyState>
        </Panel>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {d.open.map((po, i) => (
            <Panel
              key={po.id}
              title={`PO nháp ${i + 1}`}
              description={`${po.items.length} hạng mục · số PO được cấp khi chốt`}
              actions={
                canClose && (
                  <Button variant="primary" onClick={() => setClosing(po)}>
                    <Lock className="size-4" aria-hidden="true" />Chốt PO
                  </Button>
                )
              }
            >
              <Usage po={po} />
              {po.over_limit && (
                <p className="mt-2 flex items-start gap-1.5 text-sm text-failed">
                  <AlertTriangle className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
                  Một hạng mục đơn lẻ vượt hạn mức – cần phê duyệt riêng trước khi chốt.
                </p>
              )}
              <Items po={po} />
            </Panel>
          ))}
        </div>
      )}

      <Panel title="PO đã chốt" bodyClassName="p-0">
        {d.closed.length === 0 ? (
          <p className="p-4 text-sm text-muted">Chưa chốt PO nào.</p>
        ) : (
          <ul>
            {d.closed.map((po) => (
              <li key={po.id} className="flex flex-wrap items-center gap-x-4 gap-y-1 border-b border-line px-4 py-2.5 last:border-0">
                <Mono className="font-semibold">{po.number}</Mono>
                <span className="tabular">{formatVnd(po.total)}</span>
                <span className="text-sm text-muted">{po.items.length} hạng mục · chốt {formatDateTime(po.closed_at)} bởi {po.closed_by}</span>
                {canClose && (
                  <Button size="sm" className="ml-auto" onClick={() => downloadEmail(po)}>
                    <Mail className="size-4" aria-hidden="true" />Tải email nháp
                  </Button>
                )}
              </li>
            ))}
          </ul>
        )}
      </Panel>

      <Dialog
        open={closing !== null}
        onClose={() => setClosing(null)}
        title={`Chốt PO – ${formatVnd(closing?.total)}?`}
        footer={
          <>
            <Button onClick={() => setClosing(null)}>Huỷ</Button>
            <Button variant="primary" loading={close.isPending} onClick={() => closing && close.mutate(closing)}>
              <Lock className="size-4" aria-hidden="true" />Chốt và tạo email nháp
            </Button>
          </>
        }
      >
        <ul className="list-disc space-y-1 pl-5 text-sm">
          <li>Hệ thống cấp số PO, <b>khoá đơn giá</b> hiện tại của {closing?.items.length} hạng mục.</li>
          <li>Các đề xuất chuyển sang <b>Đã đặt mua</b>; đề xuất duyệt sau sẽ vào PO mới.</li>
          <li>Trình duyệt tải về file <b>.eml</b> – mở bằng Outlook là thành email nháp theo mẫu (cấu hình ở Cấu hình công việc), kèm bảng chi tiết Excel và bằng chứng Function Head. Kiểm tra rồi bấm Gửi.</li>
        </ul>
        {closing?.over_limit && <p role="alert" className="mt-3 rounded-sm bg-failed-soft px-3 py-2 text-sm text-failed">PO này vượt hạn mức.</p>}
      </Dialog>
    </div>
  );
}
