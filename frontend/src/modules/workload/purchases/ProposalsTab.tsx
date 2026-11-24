import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { AlertTriangle, Copy, LoaderCircle, Paperclip, ShoppingCart, Trash2 } from "lucide-react";
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
import { EVIDENCE_ACCEPT, EvidenceHelp, pastedImage, uploadEvidence } from "../evidence";

const ACTION_LABEL: Partial<Record<PurchaseStatus, string>> = {
  approved: "Duyệt",
  received: "Đã nhận hàng",
  rejected: "Từ chối",
  proposed: "Mở lại",
};

const ACTIVE = new Set(["proposed", "approved"]);
const dupKey = (p: Purchase) => `${p.catalog_item?.id ?? p.item.trim().toLowerCase()}|${(p.employee ?? "").trim().toLowerCase()}`;

function DupChip({ n }: { n: number }) {
  return (
    <span className="mt-0.5 inline-flex items-center gap-1 rounded-sm bg-waiting-soft px-1.5 py-0.5 text-xs text-waiting" title="Cùng thiết bị, cùng người dùng">
      <Copy className="size-3.5" aria-hidden="true" />Có thể trùng ({n} đề xuất khác)
    </span>
  );
}

export function sourceLabel(p: Purchase): string {
  if (p.ticket_display_id) return `#${p.ticket_display_id}`;
  if (p.source === "onboard") return `Onboard${p.source_ref ? ` #${p.source_ref}` : ""}`;
  return "Lập tay";
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

function ApproveDialog({ p: initial, dup, onClose, onDone }: { p: Purchase; dup: number; onClose: () => void; onDone: () => void }) {
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
  // Why "Duyệt" is disabled, announced with the button.
  const blocked = noEvidence
    ? "Chưa có bằng chứng Function Head."
    : !cat
      ? "Chọn thiết bị trong bảng giá."
      : byQuote && p.quote_rule?.problem
        ? "Chưa đủ báo giá."
        : !price
          ? byQuote ? "Chưa có báo giá có giá." : "Thiết bị chưa có đơn giá."
          : null;
  return (
    <Dialog
      open
      size={byQuote ? "lg" : "md"}
      onClose={onClose}
      title={`Duyệt đề xuất “${p.item}”?`}
      description="Sau khi duyệt, đề xuất tự được xếp vào PO đang mở; PO vượt hạn mức thì hệ thống mở PO tiếp theo."
      footer={
        <>
          {blocked && <span id={`approve-why-${p.id}`} className="mr-auto self-center text-sm text-waiting">{blocked}</span>}
          <Button onClick={onClose}>Huỷ</Button>
          <Button
            variant="primary"
            disabled={!!blocked}
            aria-describedby={blocked ? `approve-why-${p.id}` : undefined}
            loading={approve.isPending}
            onClick={() => approve.mutate()}
          >
            Duyệt{price ? ` – ${formatVnd(price * p.quantity)}` : ""}
          </Button>
        </>
      }
    >
      <div className="space-y-4">
        <dl className="grid grid-cols-[8rem_1fr] gap-x-3 gap-y-1 rounded-sm bg-surface-2 px-3 py-2 text-sm">
          <dt className="text-muted">Người đề xuất</dt><dd>{p.created_by_name} · {formatDate(p.created_at)}</dd>
          <dt className="text-muted">Người dùng</dt><dd>{p.employee || "—"}</dd>
          <dt className="text-muted">Nguồn</dt><dd>{sourceLabel(p)} · {KIND_LABEL[p.kind]} × {p.quantity}</dd>
          {p.reason && (<><dt className="text-muted">Lý do</dt><dd className="whitespace-pre-line">{p.reason}</dd></>)}
        </dl>
        {dup > 0 && (
          <p role="alert" className="flex items-start gap-2 rounded-sm bg-waiting-soft px-3 py-2 text-sm text-waiting">
            <Copy className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            Có {dup} đề xuất khác cùng thiết bị cho cùng người dùng đang chờ hoặc đã duyệt – kiểm tra có trùng không trước khi duyệt.
          </p>
        )}
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
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [receiving, setReceiving] = useState<Purchase | null>(null);
  const [rejecting, setRejecting] = useState<Purchase | "bulk" | null>(null);
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
  const items = q.data?.items ?? [];
  // Same device for the same person still pending or approved: probably requested twice.
  const dups = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of items) if (ACTIVE.has(p.status)) m.set(dupKey(p), (m.get(dupKey(p)) ?? 0) + 1);
    return m;
  }, [items]);
  const dupOf = (p: Purchase) => (ACTIVE.has(p.status) ? (dups.get(dupKey(p)) ?? 1) - 1 : 0);
  const selectable = items.filter((p) => p.next.some((t) => t === "approved" || t === "rejected"));
  const chosen = selectable.filter((p) => selected.has(p.id));
  const toggle = (id: string) => setSelected((cur) => { const n = new Set(cur); if (n.has(id)) n.delete(id); else n.add(id); return n; });
  const bulk = useMutation({
    mutationFn: ({ to, note }: { to: "approved" | "rejected"; note?: string }) =>
      api<{ done: string[]; failed: Record<string, string> }>("/api/workload/purchases/bulk", { method: "POST", json: { ids: chosen.map((p) => p.id), status: to, note: note ?? "" } }),
    onSuccess: (r, v) => {
      const verb = v.to === "approved" ? "duyệt" : "từ chối";
      const failed = Object.entries(r.failed);
      if (r.done.length) toast.success(`Đã ${verb} ${r.done.length} đề xuất.`);
      if (failed.length) {
        const name = (id: string) => items.find((p) => p.id === id)?.item ?? id;
        toast.error(`${failed.length} đề xuất chưa ${verb} được: ${failed.map(([id, m]) => `${name(id)} – ${m}`).join("; ")}`, { duration: 10000 });
      }
      setSelected(new Set(Object.keys(r.failed)));
      setRejecting(null);
      refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const counts = q.data?.counts ?? {};
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const manage = can("workload.purchase.manage");
  const priced = can("workload.price.view");

  const actions = (p: Purchase) => (
    <span className="flex flex-wrap gap-1.5">
      {p.next.filter((to) => ACTION_LABEL[to]).map((to) => (
        <Button
          key={to}
          size="sm"
          variant={to === "rejected" ? "ghost" : "secondary"}
          className={cn(to === "rejected" && "text-failed")}
          loading={move.isPending && move.variables?.id === p.id && move.variables?.to === to}
          onClick={() => (to === "rejected" ? (setNote(""), setRejecting(p)) : to === "approved" ? setApproving(p) : move.mutate({ id: p.id, to }))}
        >
          {ACTION_LABEL[to]}
        </Button>
      ))}
      {p.status === "ordered" && !p.receive_job && (
        <Button size="sm" variant="secondary" onClick={() => setReceiving(p)}>Nhận hàng</Button>
      )}
    </span>
  );

  const columns = useMemo<ColumnDef<Purchase, unknown>[]>(
    () => [
      ...(manage
        ? [
            {
              id: "select",
              enableSorting: false,
              header: () => (
                <input
                  type="checkbox"
                  className="size-4 accent-primary"
                  aria-label="Chọn tất cả đề xuất có thể duyệt hoặc từ chối"
                  checked={selectable.length > 0 && chosen.length === selectable.length}
                  onChange={(e) => setSelected(new Set(e.target.checked ? selectable.map((p) => p.id) : []))}
                />
              ),
              cell: (c: { row: { original: Purchase } }) =>
                selectable.includes(c.row.original) ? (
                  <input type="checkbox" className="size-4 accent-primary" aria-label={`Chọn ${c.row.original.item}`} checked={selected.has(c.row.original.id)} onChange={() => toggle(c.row.original.id)} />
                ) : null,
            } as ColumnDef<Purchase, unknown>,
          ]
        : []),
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
              {dupOf(p) > 0 && <DupChip n={dupOf(p)} />}
            </button>
          );
        },
      },
      ...(priced
        ? [{ accessorKey: "amount", header: "Thành tiền", cell: (c: { row: { original: Purchase } }) => <span className="tabular whitespace-nowrap">{formatVnd(c.row.original.amount, "Chưa có giá")}</span> } as ColumnDef<Purchase, unknown>]
        : []),
      { id: "source", header: "Nguồn", accessorFn: (p) => sourceLabel(p), cell: (c) => <Mono className="whitespace-nowrap">{sourceLabel(c.row.original)}</Mono> },
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
        ? [{ id: "po", header: "PO", accessorFn: (p: Purchase) => p.po?.number ?? "", cell: (c: { row: { original: Purchase } }) => (c.row.original.po ? <Mono className="whitespace-nowrap">{c.row.original.po.number ?? "PO nháp"}</Mono> : "—") } as ColumnDef<Purchase, unknown>]
        : []),
      { accessorKey: "created_by_name", header: "Người đề xuất", cell: (c) => <span className="whitespace-nowrap" title={c.row.original.created_by}>{c.row.original.created_by_name}</span> },
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
              cell: (c: { row: { original: Purchase } }) => actions(c.row.original),
            } as ColumnDef<Purchase, unknown>,
          ]
        : []),
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [manage, priced, move.isPending, move.variables, selected, items, dups],
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
      {manage && chosen.length > 0 && (
        <div role="region" aria-label="Thao tác hàng loạt" className="sticky top-0 z-20 mb-2 flex flex-wrap items-center gap-2 rounded-md border border-primary/40 bg-primary-soft px-3 py-2 text-sm">
          <span className="font-medium">Đã chọn {chosen.length} đề xuất</span>
          <Button size="sm" variant="primary" loading={bulk.isPending && bulk.variables?.to === "approved"} onClick={() => bulk.mutate({ to: "approved" })}>Duyệt {chosen.length}</Button>
          <Button size="sm" variant="ghost" className="text-failed" onClick={() => { setNote(""); setRejecting("bulk"); }}>Từ chối {chosen.length}</Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected(new Set())}>Bỏ chọn</Button>
          <HelpTip title="Duyệt hàng loạt">
            <span className="block">Mỗi đề xuất vẫn được kiểm tra như khi duyệt riêng: phải có bằng chứng Function Head và giá trong Bảng giá.</span>
            <span className="block">PC/Laptop cần báo giá sẽ không duyệt được hàng loạt – mở “Duyệt” từng đề xuất để thêm báo giá. Đề xuất không đạt được giữ nguyên và báo lý do.</span>
          </HelpTip>
        </div>
      )}
      {q.isLoading ? (
        <div className="rounded-md border border-line bg-surface"><LoadingRows rows={6} /></div>
      ) : (
        <>
          <div className="hidden md:block">
            <DataTable
              data={items}
              columns={columns}
              caption="Đề xuất mua thiết bị"
              getRowId={(p) => p.id}
              empty={<EmptyState icon={ShoppingCart} title="Chưa có đề xuất">Operator lập đề xuất từ gợi ý ở trang Việc của tôi hoặc Kho & cấp phát, hoặc bấm “Đề xuất mới”.</EmptyState>}
            />
          </div>
          {/* Phone: one card per proposal, actions visible without horizontal scrolling. */}
          <ul className="space-y-2 md:hidden" aria-label="Đề xuất mua thiết bị">
            {items.length === 0 && <li><EmptyState icon={ShoppingCart} title="Chưa có đề xuất">Bấm “Đề xuất mới” hoặc lập từ gợi ý ở Việc của tôi.</EmptyState></li>}
            {items.map((p) => (
              <li key={p.id} className="rounded-md border border-line bg-surface p-3 shadow-card">
                <div className="flex items-start gap-2">
                  {manage && selectable.includes(p) && (
                    <input type="checkbox" className="mt-1 size-5 accent-primary" aria-label={`Chọn ${p.item}`} checked={selected.has(p.id)} onChange={() => toggle(p.id)} />
                  )}
                  <button type="button" className="min-w-0 flex-1 text-left" onClick={() => setDetail(p)}>
                    <span className="block font-medium text-primary">{p.catalog_item?.name ?? p.item} × {p.quantity}</span>
                    <span className="block text-sm text-muted">{p.employee || KIND_LABEL[p.kind]} · {p.created_by_name} · {formatDate(p.created_at)}</span>
                  </button>
                  <StatusBadge {...PURCHASE_STATUS[p.status]!} />
                </div>
                <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1 text-sm">
                  {priced && <span className="tabular">{formatVnd(p.amount, "Chưa có giá")}</span>}
                  <span className={p.evidence.length ? "" : "text-waiting"}>{p.evidence.length ? `${p.evidence.length} bằng chứng` : "Chưa có bằng chứng"}</span>
                  {dupOf(p) > 0 && <DupChip n={dupOf(p)} />}
                </div>
                <ReceiveState p={p} onRetried={refresh} />
                {manage && <div className="mt-2">{actions(p)}</div>}
              </li>
            ))}
          </ul>
        </>
      )}
      {detail && <DetailDialog p={detail} onClose={() => setDetail(null)} onChange={(p) => { setDetail(p); refresh(); }} />}
      {receiving && <ReceiveDialog p={receiving} onClose={() => setReceiving(null)} onDone={() => { setReceiving(null); refresh(); }} />}
      {approving && <ApproveDialog p={approving} dup={dupOf(approving)} onClose={() => setApproving(null)} onDone={() => { setApproving(null); refresh(); }} />}
      <Dialog
        open={rejecting !== null}
        onClose={() => setRejecting(null)}
        title={rejecting === "bulk" ? `Từ chối ${chosen.length} đề xuất đã chọn?` : `Từ chối “${rejecting?.item ?? ""}”?`}
        description={rejecting !== "bulk" && rejecting?.po ? "Đề xuất sẽ được rút khỏi PO nháp và các PO được gom lại." : undefined}
        size="sm"
        footer={
          <>
            <Button onClick={() => setRejecting(null)}>Huỷ</Button>
            <Button
              variant="danger"
              disabled={note.trim().length < 3}
              loading={move.isPending || bulk.isPending}
              onClick={() => (rejecting === "bulk" ? bulk.mutate({ to: "rejected", note }) : rejecting && move.mutate({ id: rejecting.id, to: "rejected", note }))}
            >
              Từ chối
            </Button>
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
