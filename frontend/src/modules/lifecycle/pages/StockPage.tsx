import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Archive, ArchiveRestore, PackageCheck, PackageX, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { cn } from "@/core/lib/cn";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { ConfirmDangerDialog } from "@/core/ui/confirm-danger-dialog";
import { DataTable } from "@/core/ui/data-table";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge, type Status } from "@/core/ui/status";

type StockStatus = "new" | "returned" | "pending";
interface StockItem {
  id: string;
  name: string | null;
  asset_tag: string | null;
  serial: string | null;
  product: string | null;
  product_type: string | null;
  status: StockStatus;
  returned_at: string | null;
  returned_from: string | null;
  reserved_for: { name: string | null; display_id: string } | null;
  missing: ("asset_tag" | "serial")[];
}
interface Stock {
  as_of: string | null;
  items: StockItem[];
  states: { in_store: string; pending: string; disposed: string };
  can: { manage: boolean; dispose: boolean };
}

const STATUS: Record<StockStatus, { label: string; status: Status }> = {
  new: { label: "Mới", status: "done" },
  returned: { label: "Thu hồi", status: "pending" },
  pending: { label: "Chờ thanh lý", status: "warning" },
};
type View = StockStatus | "all" | "missing";
const KEY = ["lifecycle", "stock"] as const;

/** Tồn kho: every unassigned asset in store or waiting for disposal, with bulk state changes. */
export function StockPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: KEY, queryFn: () => api<Stock>("/api/lifecycle/stock") });
  const [view, setView] = useState<View>("new");
  const [search, setSearch] = useState("");
  const [type, setType] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [disposeOpen, setDisposeOpen] = useState(false);
  const d = q.data;
  const all = d?.items ?? [];
  const types = useMemo(() => [...new Set(all.map((i) => i.product_type ?? "Khác"))].sort(), [all]);
  const count = (v: View) => all.filter((i) => (v === "all" ? true : v === "missing" ? i.missing.length > 0 : i.status === v)).length;
  const needle = search.trim().toLowerCase();
  const shown = all.filter(
    (i) =>
      (view === "all" || (view === "missing" ? i.missing.length > 0 : i.status === view)) &&
      (!type || (i.product_type ?? "Khác") === type) &&
      (!needle || [i.asset_tag, i.serial, i.name, i.product].some((x) => x?.toLowerCase().includes(needle))),
  );
  const selected = all.filter((i) => picked.includes(i.id));
  const shownIds = shown.map((i) => i.id);
  const allOn = shownIds.length > 0 && shownIds.every((id) => picked.includes(id));
  const go = (v: View) => {
    setView(v);
    setPicked([]);
  };

  const refresh = () => qc.invalidateQueries({ queryKey: KEY });
  const mark = useMutation({
    mutationFn: (returned: boolean) => api("/api/lifecycle/stock/returned", { method: "POST", json: { asset_ids: picked, returned } }),
    onSuccess: async (_r, returned) => {
      toast.success(returned ? `Đã đánh dấu ${picked.length} tài sản là hàng thu hồi.` : `Đã chuyển ${picked.length} tài sản về hàng mới.`);
      setPicked([]);
      await refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const change = useMutation({
    mutationFn: (to: "pending" | "in_store" | "disposed") =>
      api<{ job_id: string }>("/api/lifecycle/stock/state", {
        method: "POST",
        json: { to, assets: selected.map((i) => ({ id: i.id, tag: i.asset_tag ?? "", name: i.name ?? "" })) },
      }),
    onSuccess: (r, to) => {
      const what = to === "disposed" ? "thanh lý" : to === "pending" ? "chuyển sang Chờ thanh lý" : "trả về kho";
      toast.success(`Đang ${what} ${selected.length} tài sản trên SDP.`, {
        description: "Danh sách cập nhật sau lần đồng bộ kế tiếp.",
        action: { label: "Xem Job", onClick: () => window.location.assign(`/lifecycle/jobs/${r.job_id}`) },
      });
      setPicked([]);
      setDisposeOpen(false);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const columns = useMemo<ColumnDef<StockItem, unknown>[]>(() => {
    const pick: ColumnDef<StockItem, unknown>[] = d?.can.manage || d?.can.dispose
      ? [
          {
            id: "select",
            enableSorting: false,
            header: () => (
              <input
                type="checkbox"
                className="size-4 accent-[var(--primary)]"
                aria-label="Chọn tất cả tài sản đang hiện"
                checked={allOn}
                disabled={!shownIds.length}
                onChange={() => setPicked(allOn ? picked.filter((x) => !shownIds.includes(x)) : [...new Set([...picked, ...shownIds])])}
              />
            ),
            cell: (c) => (
              <input
                type="checkbox"
                className="size-4 accent-[var(--primary)]"
                aria-label={`Chọn ${c.row.original.asset_tag ?? c.row.original.name ?? c.row.original.id}`}
                checked={picked.includes(c.row.original.id)}
                onChange={() => setPicked((p) => (p.includes(c.row.original.id) ? p.filter((x) => x !== c.row.original.id) : [...p, c.row.original.id]))}
              />
            ),
          },
        ]
      : [];
    const missing = (label: string) => <StatusBadge status="failed" label={`Thiếu ${label}`} />;
    return [
      ...pick,
      { accessorKey: "asset_tag", header: "Mã tài sản", cell: (c) => (c.row.original.asset_tag ? <Mono>{c.row.original.asset_tag}</Mono> : missing("mã TS")) },
      { accessorKey: "serial", header: "Serial", cell: (c) => (c.row.original.serial ? <Mono>{c.row.original.serial}</Mono> : missing("serial")) },
      { accessorKey: "product_type", header: "Loại", cell: (c) => c.row.original.product_type ?? "—" },
      {
        accessorKey: "name",
        header: "Tên tài sản",
        cell: (c) => (
          <span className="block max-w-64">
            <span className="block truncate font-medium">{c.row.original.name ?? "—"}</span>
            {c.row.original.product && <span className="block truncate text-xs text-muted">{c.row.original.product}</span>}
          </span>
        ),
      },
      { accessorKey: "status", header: "Trạng thái", cell: (c) => <StatusBadge {...STATUS[c.row.original.status]} /> },
      {
        id: "note",
        header: "Ghi chú",
        enableSorting: false,
        cell: (c) => {
          const i = c.row.original;
          return (
            <span className="block text-sm text-muted">
              {i.reserved_for && <span className="block text-ink">Dành cho {i.reserved_for.name} (#{i.reserved_for.display_id})</span>}
              {i.status === "returned" && (
                <span className="block">Thu hồi{i.returned_from ? ` từ ${i.returned_from}` : ""}{i.returned_at ? ` · ${formatDate(i.returned_at)}` : ""}</span>
              )}
            </span>
          );
        },
      },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [d?.can.manage, d?.can.dispose, picked, allOn, shownIds.join()]);

  const reservedPicked = selected.filter((i) => i.reserved_for);
  const tabs: { v: View; label: string }[] = [
    { v: "new", label: "Mới" },
    { v: "returned", label: "Thu hồi" },
    { v: "pending", label: "Chờ thanh lý" },
    { v: "all", label: "Tất cả" },
  ];

  return (
    <>
      <PageHeader
        title="Tồn kho"
        description={
          <>
            Tài sản chưa gán người dùng trên ServiceDesk Plus: hàng mới, hàng thu hồi và hàng chờ thanh lý. Chọn nhiều tài sản để cập nhật cùng lúc.{" "}
            <HelpTip title="Trạng thái tồn kho">
              <span className="block"><b>Mới</b> và <b>Thu hồi</b>: “{d?.states.in_store ?? "In Store"}” trên SDP. Hàng thu hồi do đồng bộ tự nhận ra (lần trước đang gán cho ai đó, nay về kho) hoặc đánh dấu tay.</span>
              <span className="block"><b>Chờ thanh lý</b>: trạng thái “{d?.states.pending ?? "Chờ thanh lý"}” trên SDP. <b>Thanh lý</b> đổi sang “{d?.states.disposed ?? "Disposed"}” và tài sản rời khỏi tồn kho – chỉ Manager và Administrator.</span>
              <span className="block">Mọi thay đổi trên SDP chạy bằng API key của chính bạn (Khoá API của tôi). Mã tài sản và serial là bắt buộc – tài sản thiếu được đánh dấu đỏ để bổ sung trên SDP.</span>
            </HelpTip>
          </>
        }
      />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading || !d ? (
        !q.isError && <LoadingRows rows={8} />
      ) : !d.as_of ? (
        <Panel>
          <EmptyState icon={Archive} title="Chưa có dữ liệu kho">
            Chạy đồng bộ ở trang <Link className="text-primary underline" to="/lifecycle/sync">Đồng bộ dữ liệu</Link> để đọc tài sản từ ServiceDesk Plus.
          </EmptyState>
        </Panel>
      ) : (
        <>
          <p className="mb-3 text-sm text-muted">Theo dữ liệu đồng bộ lúc {formatDateTime(d.as_of)}.</p>
          <div className="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label="Lọc theo trạng thái">
            {tabs.map((t) => (
              <button
                key={t.v}
                type="button"
                aria-pressed={view === t.v}
                onClick={() => go(t.v)}
                className={cn(
                  "h-8 cursor-pointer rounded-full border px-3 text-sm transition-colors duration-150",
                  view === t.v ? "border-primary bg-primary-soft font-medium text-primary" : "border-line-strong bg-surface hover:bg-surface-2",
                )}
              >
                {t.label} <span className="tabular">({count(t.v)})</span>
              </button>
            ))}
            {count("missing") > 0 && (
              <button
                type="button"
                aria-pressed={view === "missing"}
                onClick={() => go("missing")}
                className={cn(
                  "h-8 cursor-pointer rounded-full border px-3 text-sm",
                  view === "missing" ? "border-failed bg-failed-soft font-medium text-failed" : "border-failed text-failed hover:bg-failed-soft",
                )}
              >
                Thiếu mã TS / serial <span className="tabular">({count("missing")})</span>
              </button>
            )}
          </div>
          <form role="search" aria-label="Lọc tồn kho" className="mb-3 grid gap-3 sm:grid-cols-[2fr_1fr]" onSubmit={(e) => e.preventDefault()}>
            <Field label="Tìm mã tài sản, serial hoặc tên">
              <Input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Ví dụ: HCM-2026-LT-1234 hoặc 5CD43…" />
            </Field>
            <Field label="Loại tài sản">
              <Select value={type} onChange={(e) => setType(e.target.value)}>
                <option value="">Tất cả loại</option>
                {types.map((t) => <option key={t} value={t}>{t}</option>)}
              </Select>
            </Field>
          </form>
          {picked.length > 0 && (
            <div role="region" aria-label="Thao tác với tài sản đã chọn" className="mb-3 flex flex-wrap items-center gap-2 rounded-md border border-primary bg-primary-soft px-3 py-2 text-sm">
              <span className="mr-1">Đã chọn <strong className="tabular">{picked.length}</strong> tài sản</span>
              {d.can.manage && selected.some((i) => i.status === "new") && (
                <Button size="sm" onClick={() => mark.mutate(true)} loading={mark.isPending}>
                  <ArchiveRestore className="size-4" aria-hidden="true" />Đánh dấu thu hồi
                </Button>
              )}
              {d.can.manage && selected.some((i) => i.status === "returned") && (
                <Button size="sm" onClick={() => mark.mutate(false)} loading={mark.isPending}>
                  <Undo2 className="size-4" aria-hidden="true" />Chuyển về hàng mới
                </Button>
              )}
              {d.can.manage && selected.every((i) => i.status !== "pending") && (
                <Button size="sm" variant="primary" onClick={() => change.mutate("pending")} loading={change.isPending}>
                  <PackageX className="size-4" aria-hidden="true" />Chuyển Chờ thanh lý
                </Button>
              )}
              {d.can.manage && selected.every((i) => i.status === "pending") && (
                <Button size="sm" onClick={() => change.mutate("in_store")} loading={change.isPending}>
                  <PackageCheck className="size-4" aria-hidden="true" />Trả về kho
                </Button>
              )}
              {d.can.dispose && selected.every((i) => i.status === "pending") && (
                <Button size="sm" variant="danger" onClick={() => setDisposeOpen(true)}>
                  <Archive className="size-4" aria-hidden="true" />Thanh lý trên SDP…
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setPicked([])}>Bỏ chọn</Button>
              {selected.some((i) => i.status === "pending") && selected.some((i) => i.status !== "pending") && (
                <span className="basis-full text-muted">Chọn riêng hàng Chờ thanh lý để thanh lý hoặc trả về kho.</span>
              )}
            </div>
          )}
          <DataTable
            data={shown}
            columns={columns}
            caption="Danh sách tồn kho"
            getRowId={(r) => r.id}
            empty={
              <EmptyState icon={Archive} title={all.length ? "Không có tài sản phù hợp" : "Kho trống"}>
                {all.length ? "Đổi bộ lọc hoặc tab trạng thái để xem tài sản khác." : "Không có tài sản nào chưa gán người dùng."}
              </EmptyState>
            }
          />
          <ConfirmDangerDialog
            open={disposeOpen}
            onClose={() => setDisposeOpen(false)}
            onConfirm={() => change.mutate("disposed")}
            loading={change.isPending}
            title={`Thanh lý ${selected.length} tài sản`}
            consequences={
              <>
                <span className="block">Đổi trạng thái trên ServiceDesk Plus thành “{d.states.disposed}”; tài sản rời khỏi tồn kho. Việc này ghi bằng API key của bạn và được lưu nhật ký.</span>
                <span className="mt-1 block font-mono text-sm">{selected.slice(0, 8).map((i) => i.asset_tag ?? i.name ?? i.id).join(", ")}{selected.length > 8 ? ` và ${selected.length - 8} tài sản khác` : ""}</span>
                {reservedPicked.length > 0 && (
                  <span className="mt-1 block text-waiting">{reservedPicked.length} tài sản đang được dành cho nhân viên sắp onboard.</span>
                )}
              </>
            }
            confirmText="THANH LY"
            inputLabel={<>Nhập <strong className="font-mono">THANH LY</strong> để xác nhận</>}
            confirmLabel="Thanh lý"
          />
        </>
      )}
    </>
  );
}
