import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { AlertTriangle, LoaderCircle, Paperclip, ShoppingCart, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { cn } from "@/core/lib/cn";
import { formatDate, formatVnd } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono } from "@/core/ui/feedback";
import { Field, Select, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import { useCatalog, type Purchase, type PurchaseStatus } from "../api";
import { KIND_LABEL, PURCHASE_STATUS } from "../labels";
import { QuotesBlock } from "./Quotes";
import { ReceiveDialog } from "./ReceiveDialog";
import { EVIDENCE_ACCEPT, EvidenceHelp, pastedImage, uploadEvidence } from "../PurchaseDialog";

const ACTION_LABEL: Partial<Record<PurchaseStatus, string>> = {
  approved: "Duyệt",
  received: "Đã nhận hàng",
  rejected: "Từ chối",
  proposed: "Mở lại",
};

export function sourceLabel(p: Purchase): string {
  if (p.ticket_display_id) return `#${p.ticket_display_id}`;
  if (p.source === "onboard") return `Onboard${p.source_ref ? ` #${p.source_ref}` : ""}`;
  return "—";
}

/** Evidence files of one proposal: open, add (file / pasted screenshot) and remove while not yet approved. */
function EvidenceBlock({ p, onChange }: { p: Purchase; onChange: (p: Purchase) => void }) {
  const { me, can } = useAuth();
  const editable = p.status === "proposed" && (p.created_by === me?.upn || can("workload.purchase.manage"));
  const add = useMutation({
    mutationFn: (f: File) => uploadEvidence(p.id, f),
    onSuccess: (np) => { onChange(np); toast.success("Đã đính kèm bằng chứng."); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api<Purchase>(`/api/workload/purchases/${p.id}/evidence/${id}`, { method: "DELETE" }),
    onSuccess: onChange,
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <section aria-label="Bằng chứng Function Head" onPaste={(e) => { const f = editable && pastedImage(e); if (f) add.mutate(f); }}>
      <h3 className="mb-1 flex items-center gap-1 text-sm font-semibold">Bằng chứng Function Head đã duyệt ({p.evidence.length}) <EvidenceHelp /></h3>
      {p.evidence.length === 0 && <p className="text-sm text-waiting">Chưa có bằng chứng – chưa duyệt được.</p>}
      <ul className="space-y-1 text-sm">
        {p.evidence.map((e) => (
          <li key={e.id} className="flex items-center gap-2">
            <Paperclip className="size-3.5 shrink-0 text-muted" aria-hidden="true" />
            <a className="truncate text-primary underline-offset-2 hover:underline" href={`/api/workload/purchases/${p.id}/evidence/${e.id}`} target="_blank" rel="noreferrer">{e.filename}</a>
            <span className="shrink-0 text-xs text-muted">{e.created_by}</span>
            {editable && (
              <Button size="icon" variant="ghost" aria-label={`Xoá ${e.filename}`} loading={remove.isPending && remove.variables === e.id} onClick={() => remove.mutate(e.id)}>
                <Trash2 className="size-4" aria-hidden="true" />
              </Button>
            )}
          </li>
        ))}
      </ul>
      {editable && (
        <label className="mt-2 inline-flex h-8 cursor-pointer items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 text-sm font-medium hover:bg-surface-2 focus-within:outline-2 focus-within:outline-primary">
          <Paperclip className="size-4" aria-hidden="true" />{add.isPending ? "Đang tải lên…" : "Thêm file / ảnh"}
          <input type="file" accept={EVIDENCE_ACCEPT} className="sr-only" disabled={add.isPending} onChange={(e) => { const f = e.target.files?.[0]; if (f) add.mutate(f); e.target.value = ""; }} />
        </label>
      )}
    </section>
  );
}

/** Progress of the "nhập kho SDP" worker job; failed jobs can be retried (the step is idempotent). */
function ReceiveState({ p, onRetried }: { p: Purchase; onRetried: () => void }) {
  const retry = useMutation({
    mutationFn: (id: string) => api(`/api/core/jobs/${id}/steps/receive/retry`, { method: "POST" }),
    onSuccess: () => { toast.success("Đã yêu cầu nhập kho lại."); onRetried(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const j = p.receive_job;
  if (!j || p.status !== "ordered") return null;
  if (j.status === "failed")
    return (
      <span className="mt-1 block max-w-56 text-xs text-failed">
        <span className="flex items-center gap-1" title={j.error ?? ""}><AlertTriangle className="size-3.5 shrink-0" aria-hidden="true" /><span className="truncate">Nhập kho SDP lỗi: {j.error}</span></span>
        <Button size="sm" className="mt-1" loading={retry.isPending} onClick={() => retry.mutate(j.id)}>Thử lại</Button>
      </span>
    );
  return <span className="mt-1 flex items-center gap-1 text-xs text-progress"><LoaderCircle className="size-3.5 motion-safe:animate-spin" aria-hidden="true" />Đang nhập kho SDP…</span>;
}

function DetailDialog({ p, onClose, onChange }: { p: Purchase; onClose: () => void; onChange: (p: Purchase) => void }) {
  const priced = useAuth().can("workload.price.view");
  return (
    <Dialog open onClose={onClose} title={p.catalog_item?.name ?? p.item} description={`${KIND_LABEL[p.kind]} × ${p.quantity} · ${sourceLabel(p)}${p.employee ? ` · ${p.employee}` : ""}`} footer={<Button onClick={onClose}>Đóng</Button>}>
      <dl className="mb-4 grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 text-sm">
        <dt className="text-muted">Trạng thái</dt><dd><StatusBadge {...PURCHASE_STATUS[p.status]!} /></dd>
        {priced && (
          <>
            <dt className="text-muted">Đơn giá</dt><dd className="tabular">{formatVnd(p.unit_price)}{p.quotes.length > 0 && ` (thấp nhất trong ${p.quotes.length} báo giá)`}</dd>
            <dt className="text-muted">Thành tiền</dt><dd className="tabular">{formatVnd(p.amount)}</dd>
            <dt className="text-muted">PO</dt><dd>{p.po ? (p.po.number ?? "PO nháp (chưa chốt)") : "—"}</dd>
          </>
        )}
        <dt className="text-muted">Người đề xuất</dt><dd>{p.created_by} · {formatDate(p.created_at)}</dd>
        {p.approved_by && (<><dt className="text-muted">Người duyệt</dt><dd>{p.approved_by}</dd></>)}
        {p.reason && (<><dt className="text-muted">Lý do</dt><dd className="whitespace-pre-line">{p.reason}</dd></>)}
        {p.note && (<><dt className="text-muted">Ghi chú</dt><dd>{p.note}</dd></>)}
        {p.received_assets.length > 0 && (
          <><dt className="text-muted">Tài sản SDP</dt><dd>{p.received_assets.map((a) => `${a.asset_tag || a.serial} (#${a.id})`).join(", ")}</dd></>
        )}
      </dl>
      <EvidenceBlock p={p} onChange={onChange} />
    </Dialog>
  );
}

function ApproveDialog({ p: initial, onClose, onDone }: { p: Purchase; onClose: () => void; onDone: () => void }) {
  const catalog = useCatalog();
  const [p, setP] = useState(initial);
  const [catId, setCatId] = useState(p.catalog_item?.id ?? "");
  const cat = catalog.data?.find((c) => c.id === catId);
  const approve = useMutation({
    mutationFn: () => api<Purchase>(`/api/workload/purchases/${p.id}`, { method: "PATCH", json: { status: "approved", catalog_item_id: catId || null } }),
    onSuccess: (np) => {
      toast.success(`Đã duyệt “${np.catalog_item?.name ?? np.item}” và xếp vào ${np.po?.number ?? "PO nháp"}.`);
      onDone();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const byQuote = !!cat?.needs_quotes;
  const price = byQuote ? (p.quotes.find((q) => q.lowest)?.unit_price ?? null) : (cat?.unit_price ?? null);
  const noEvidence = p.evidence.length === 0;
  return (
    <Dialog
      open
      size={byQuote ? "lg" : "md"}
      onClose={onClose}
      title={`Duyệt đề xuất “${p.item}”?`}
      description="Sau khi duyệt, đề xuất tự được xếp vào PO đang mở; PO vượt hạn mức thì hệ thống mở PO tiếp theo."
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" disabled={noEvidence || !price || (byQuote && !!p.quote_rule?.problem)} loading={approve.isPending} onClick={() => approve.mutate()}>
            Duyệt{price ? ` – ${formatVnd(price * p.quantity)}` : ""}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <Field
          label="Thiết bị trong bảng giá"
          required
          hint={
            !cat
              ? "Chọn thiết bị để định giá."
              : byQuote
                ? `Định giá theo báo giá cho đề xuất này (× ${p.quantity}).`
                : cat.unit_price
                  ? `Đơn giá ${formatVnd(cat.unit_price)} × ${p.quantity} (theo bảng giá)`
                  : "Chưa có đơn giá – cập nhật ở thẻ Bảng giá (từ file báo giá PDF)."
          }
        >
          <Select data-autofocus value={catId} onChange={(e) => setCatId(e.target.value)}>
            <option value="">— Chọn —</option>
            {(catalog.data ?? []).filter((c) => c.active).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}{c.needs_quotes ? " – theo báo giá" : c.unit_price ? ` – ${formatVnd(c.unit_price)}` : " – chưa có giá"}
              </option>
            ))}
          </Select>
        </Field>
        {byQuote && <QuotesBlock p={p} onChange={setP} />}
        {noEvidence ? (
          <p role="alert" className="rounded-sm bg-waiting-soft px-3 py-2 text-sm text-waiting">Chưa có bằng chứng Function Head duyệt – nhờ người đề xuất đính kèm trước.</p>
        ) : (
          <p className="text-sm">
            Bằng chứng Function Head:{" "}
            {p.evidence.map((e, i) => (
              <span key={e.id}>{i > 0 && ", "}<a className="text-primary underline underline-offset-2" href={`/api/workload/purchases/${p.id}/evidence/${e.id}`} target="_blank" rel="noreferrer">{e.filename}</a></span>
            ))}
          </p>
        )}
      </div>
    </Dialog>
  );
}

export function ProposalsTab() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [status, setStatus] = useState("");
  const [detail, setDetail] = useState<Purchase | null>(null);
  const [approving, setApproving] = useState<Purchase | null>(null);
  const [receiving, setReceiving] = useState<Purchase | null>(null);
  const [rejecting, setRejecting] = useState<Purchase | null>(null);
  const [note, setNote] = useState("");
  const q = useQuery({
    queryKey: ["workload", "purchases", status],
    queryFn: () => api<{ items: Purchase[]; counts: Record<string, number> }>(`/api/workload/purchases?${new URLSearchParams({ status })}`),
  });
  const refresh = () => void qc.invalidateQueries({ queryKey: ["workload"] });
  const move = useMutation({
    mutationFn: ({ id, to, note }: { id: string; to: PurchaseStatus; note?: string }) =>
      api<Purchase>(`/api/workload/purchases/${id}`, { method: "PATCH", json: { status: to, note: note ?? "" } }),
    onSuccess: (p) => {
      toast.success(`“${p.item}”: ${PURCHASE_STATUS[p.status]!.label}.`);
      setRejecting(null);
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const counts = q.data?.counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const manage = can("workload.purchase.manage");
  const priced = can("workload.price.view");

  const columns = useMemo<ColumnDef<Purchase, unknown>[]>(
    () => [
      { accessorKey: "created_at", header: "Ngày", cell: (c) => <span className="tabular whitespace-nowrap">{formatDate(c.row.original.created_at)}</span> },
      {
        accessorKey: "item",
        header: "Thiết bị",
        cell: (c) => {
          const p = c.row.original;
          return (
            <button type="button" className="block max-w-80 text-left" onClick={() => setDetail(p)}>
              <span className="block font-medium text-primary underline-offset-2 hover:underline">{p.catalog_item?.name ?? p.item} × {p.quantity}</span>
              <span className="block truncate text-xs text-muted">{KIND_LABEL[p.kind]}{p.employee ? ` · ${p.employee}` : ""}</span>
            </button>
          );
        },
      },
      ...(priced
        ? [{ accessorKey: "amount", header: "Thành tiền", cell: (c: { row: { original: Purchase } }) => <span className="tabular whitespace-nowrap">{formatVnd(c.row.original.amount, "Chưa có giá")}</span> } as ColumnDef<Purchase, unknown>]
        : []),
      { id: "source", header: "Nguồn", accessorFn: (p) => sourceLabel(p), cell: (c) => <Mono>{sourceLabel(c.row.original)}</Mono> },
      {
        id: "evidence",
        header: "Bằng chứng",
        accessorFn: (p) => p.evidence.length,
        cell: (c) => {
          const n = c.row.original.evidence.length;
          return n ? (
            <span className="inline-flex items-center gap-1 tabular"><Paperclip className="size-3.5" aria-hidden="true" />{n} file</span>
          ) : (
            <span className="text-waiting">Chưa có</span>
          );
        },
      },
      ...(priced
        ? [{ id: "po", header: "PO", accessorFn: (p: Purchase) => p.po?.number ?? "", cell: (c: { row: { original: Purchase } }) => (c.row.original.po ? <Mono>{c.row.original.po.number ?? "PO nháp"}</Mono> : "—") } as ColumnDef<Purchase, unknown>]
        : []),
      { accessorKey: "created_by", header: "Người đề xuất", cell: (c) => <span className="whitespace-nowrap">{c.row.original.created_by}</span> },
      {
        accessorKey: "status",
        header: "Trạng thái",
        cell: (c) => (
          <span className="block">
            <StatusBadge {...PURCHASE_STATUS[c.row.original.status]!} />
            <ReceiveState p={c.row.original} onRetried={refresh} />
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
                  {c.row.original.next.filter((to) => ACTION_LABEL[to]).map((to) => (
                    <Button
                      key={to}
                      size="sm"
                      variant={to === "rejected" ? "ghost" : "secondary"}
                      className={cn(to === "rejected" && "text-failed")}
                      loading={move.isPending && move.variables?.id === c.row.original.id && move.variables?.to === to}
                      onClick={() =>
                        to === "rejected" ? (setNote(""), setRejecting(c.row.original)) : to === "approved" ? setApproving(c.row.original) : move.mutate({ id: c.row.original.id, to })
                      }
                    >
                      {ACTION_LABEL[to]}
                    </Button>
                  ))}
                  {c.row.original.status === "ordered" && !c.row.original.receive_job && (
                    <Button size="sm" variant="secondary" onClick={() => setReceiving(c.row.original)}>Nhận hàng</Button>
                  )}
                </span>
              ),
            } as ColumnDef<Purchase, unknown>,
          ]
        : []),
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [manage, priced, move.isPending, move.variables],
  );

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-1.5">
        <div role="group" aria-label="Lọc theo trạng thái" className="flex flex-wrap gap-1.5">
          {[["", "Tất cả", total] as const, ...Object.entries(PURCHASE_STATUS).map(([k, v]) => [k, v.label, counts[k] ?? 0] as const)].map(([k, label, n]) => (
            <Button key={k || "all"} size="sm" variant={status === k ? "primary" : "secondary"} aria-pressed={status === k} className={cn(status !== k && n === 0 && "text-muted")} onClick={() => setStatus(k)}>
              {label} <span className="tabular">{n}</span>
            </Button>
          ))}
        </div>
        <HelpTip title="Vòng đời một đề xuất">
          <span className="block"><b>Đã đề xuất</b> → Operator đính kèm bằng chứng Function Head duyệt.</span>
          <span className="block"><b>Đã duyệt</b> → Supervisor định giá: PC/Laptop theo báo giá của đối tác (trên 20 triệu cần 3 báo giá, lấy giá thấp nhất), thiết bị khác theo Bảng giá; đề xuất tự vào PO đang mở.</span>
          <span className="block"><b>Đã đặt mua</b> → khi Supervisor chốt PO chứa đề xuất.</span>
          <span className="block"><b>Đã nhận hàng</b> → bấm khi thiết bị về kho. <b>Từ chối</b> cần lý do và có thể mở lại.</span>
        </HelpTip>
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
          empty={<EmptyState icon={ShoppingCart} title="Chưa có đề xuất">Operator lập đề xuất từ gợi ý ở trang Việc của tôi hoặc Kho & cấp phát, hoặc bấm “Đề xuất mới”.</EmptyState>}
        />
      )}
      {detail && <DetailDialog p={detail} onClose={() => setDetail(null)} onChange={(p) => { setDetail(p); refresh(); }} />}
      {receiving && <ReceiveDialog p={receiving} onClose={() => setReceiving(null)} onDone={() => { setReceiving(null); refresh(); }} />}
      {approving && <ApproveDialog p={approving} onClose={() => setApproving(null)} onDone={() => { setApproving(null); refresh(); }} />}
      <Dialog
        open={rejecting !== null}
        onClose={() => setRejecting(null)}
        title={`Từ chối “${rejecting?.item ?? ""}”?`}
        description={rejecting?.po ? "Đề xuất sẽ được rút khỏi PO nháp và các PO được gom lại." : undefined}
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
