import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Download, Plus, ShoppingCart } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Textarea } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { formatDate } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import type { Purchase, PurchaseStatus } from "../api";
import { KIND_LABEL, PURCHASE_STATUS } from "../labels";
import { PurchaseDialog } from "../PurchaseDialog";

const ACTION_LABEL: Record<PurchaseStatus, string> = {
  approved: "Duyệt",
  ordered: "Đã đặt mua",
  received: "Đã nhận hàng",
  rejected: "Từ chối",
  proposed: "Mở lại",
};

export function PurchasesPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [creating, setCreating] = useState(false);
  const [rejecting, setRejecting] = useState<Purchase | null>(null);
  const [note, setNote] = useState("");
  const q = useQuery({
    queryKey: ["workload", "purchases", status],
    queryFn: () => api<{ items: Purchase[]; counts: Record<string, number> }>(`/api/workload/purchases?${new URLSearchParams({ status })}`),
  });
  const move = useMutation({
    mutationFn: ({ id, to, note }: { id: string; to: PurchaseStatus; note?: string }) =>
      api<Purchase>(`/api/workload/purchases/${id}`, { method: "PATCH", json: { status: to, note: note ?? "" } }),
    onSuccess: (p) => {
      toast.success(`“${p.item}”: ${PURCHASE_STATUS[p.status]!.label}.`);
      setRejecting(null);
      void qc.invalidateQueries({ queryKey: ["workload", "purchases"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const counts = q.data?.counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const manage = can("workload.purchase.manage");

  const columns = useMemo<ColumnDef<Purchase, unknown>[]>(
    () => [
      { accessorKey: "created_at", header: "Ngày", cell: (c) => <span className="tabular whitespace-nowrap">{formatDate(c.row.original.created_at)}</span> },
      {
        accessorKey: "item",
        header: "Thiết bị",
        cell: (c) => (
          <span className="block max-w-80">
            <span className="block font-medium">{c.row.original.item} × {c.row.original.quantity}</span>
            {c.row.original.reason && <span className="block truncate text-xs text-muted" title={c.row.original.reason}>{c.row.original.reason}</span>}
          </span>
        ),
      },
      { accessorKey: "kind", header: "Loại", cell: (c) => KIND_LABEL[c.row.original.kind] },
      { accessorKey: "ticket_display_id", header: "Request", cell: (c) => (c.row.original.ticket_display_id ? <Mono>#{c.row.original.ticket_display_id}</Mono> : "—") },
      { accessorKey: "created_by", header: "Người đề xuất", cell: (c) => <span className="whitespace-nowrap">{c.row.original.created_by}</span> },
      {
        accessorKey: "status",
        header: "Trạng thái",
        cell: (c) => (
          <span className="block">
            <StatusBadge {...PURCHASE_STATUS[c.row.original.status]!} />
            {c.row.original.note && <span className="mt-0.5 block max-w-48 truncate text-xs text-muted" title={c.row.original.note}>{c.row.original.note}</span>}
          </span>
        ),
      },
      ...(manage
        ? [
            {
              id: "actions",
              header: "Cập nhật",
              enableSorting: false,
              cell: (c: { row: { original: Purchase } }) => (
                <span className="flex flex-wrap gap-1.5">
                  {c.row.original.next.map((to) => (
                    <Button
                      key={to}
                      size="sm"
                      variant={to === "rejected" ? "ghost" : "secondary"}
                      className={cn(to === "rejected" && "text-failed")}
                      loading={move.isPending && move.variables?.id === c.row.original.id && move.variables?.to === to}
                      onClick={() => (to === "rejected" ? (setNote(""), setRejecting(c.row.original)) : move.mutate({ id: c.row.original.id, to }))}
                    >
                      {ACTION_LABEL[to]}
                    </Button>
                  ))}
                </span>
              ),
            } as ColumnDef<Purchase, unknown>,
          ]
        : []),
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [manage, move.isPending, move.variables],
  );

  return (
    <>
      <PageHeader
        title="Đề xuất mua thiết bị"
        description="Đề xuất từ request thay thế, nâng cấp hoặc sửa chữa thiết bị. Lưu trong infra-ops và xuất Excel gửi bộ phận mua hàng – không ghi lên ServiceDesk Plus."
        actions={
          <>
            <a className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 font-medium hover:bg-surface-2" href="/api/workload/purchases/export">
              <Download className="size-4" aria-hidden="true" />Xuất Excel
            </a>
            {can("workload.purchase.create") && (
              <Button variant="primary" onClick={() => setCreating(true)}><Plus className="size-4" aria-hidden="true" />Đề xuất mới</Button>
            )}
          </>
        }
      />
      <div role="group" aria-label="Lọc theo trạng thái" className="mb-3 flex flex-wrap gap-1.5">
        {[["", "Tất cả", total] as const, ...Object.entries(PURCHASE_STATUS).map(([k, v]) => [k, v.label, counts[k] ?? 0] as const)].map(([k, label, n]) => (
          <Button key={k || "all"} size="sm" variant={status === k ? "primary" : "secondary"} aria-pressed={status === k} className={cn(status !== k && n === 0 && "text-muted")} onClick={() => setStatus(k)}>
            {label} <span className="tabular">{n}</span>
          </Button>
        ))}
      </div>
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading ? (
        <div className="rounded-md border border-line bg-surface"><LoadingRows rows={6} /></div>
      ) : (
        <DataTable
          data={q.data?.items ?? []}
          columns={columns}
          caption="Đề xuất mua thiết bị"
          getRowId={(p) => p.id}
          empty={<EmptyState icon={ShoppingCart} title="Chưa có đề xuất">Operator lập đề xuất từ gợi ý ở trang Việc của tôi, hoặc bấm “Đề xuất mới”.</EmptyState>}
        />
      )}
      {creating && <PurchaseDialog onClose={() => setCreating(false)} />}
      <Dialog
        open={rejecting !== null}
        onClose={() => setRejecting(null)}
        title={`Từ chối “${rejecting?.item ?? ""}”?`}
        size="sm"
        footer={
          <>
            <Button onClick={() => setRejecting(null)}>Huỷ</Button>
            <Button variant="danger" disabled={note.trim().length < 3} loading={move.isPending} onClick={() => rejecting && move.mutate({ id: rejecting.id, to: "rejected", note })}>Từ chối</Button>
          </>
        }
      >
        <Field label="Lý do từ chối" required hint="Người đề xuất thấy lý do này trong danh sách.">
          <Textarea data-autofocus rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      </Dialog>
    </>
  );
}
