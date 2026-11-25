import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Archive, ArchiveRestore, PackageCheck, PackageX, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { cn } from "@/core/lib/cn";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { ConfirmDangerDialog } from "@/core/ui/confirm-danger-dialog";
import { DataTable } from "@/core/ui/data-table";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { announce } from "@/core/ui/live-region";
import { StatusBadge } from "@/core/ui/status";
import { Tabs } from "@/core/ui/tabs";
import { OnboardTab, type Allocation, type PurchaseLite } from "./InventoryPage";

type Origin = "new" | "returned" | "pending";
interface StockItem {
  id: string;
  name: string | null;
  asset_tag: string | null;
  serial: string | null;
  product: string | null;
  product_type: string | null;
  status: Origin;
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

/** One vocabulary: availability (Còn trống / Giữ cho X / Chờ thanh lý) first, origin (Mới / Thu hồi) as a tag. */
type View = "free" | "reserved" | "returned" | "pending" | "all" | "missing";
const VIEWS: { v: View; label: string; test: (i: StockItem) => boolean }[] = [
  { v: "free", label: "Còn trống", test: (i) => i.status !== "pending" && !i.reserved_for },
  { v: "reserved", label: "Đang giữ", test: (i) => i.status !== "pending" && !!i.reserved_for },
  { v: "returned", label: "Hàng thu hồi", test: (i) => i.status === "returned" },
  { v: "pending", label: "Chờ thanh lý", test: (i) => i.status === "pending" },
  { v: "all", label: "Tất cả", test: () => true },
];
const MISSING = (i: StockItem) => i.missing.length > 0;
const STOCK_KEY = ["lifecycle", "stock"] as const;
const ALLOC_KEY = ["lifecycle", "allocation"] as const;

function Availability({ i, updating }: { i: StockItem; updating?: "running" | "sync" }) {
  if (updating === "running") return <StatusBadge status="in_progress" label="Đang cập nhật trên SDP…" />;
  if (updating === "sync") return <StatusBadge status="pending" label="Đã ghi SDP – chờ đồng bộ" />;
  if (i.status === "pending") return <StatusBadge status="warning" label="Chờ thanh lý" />;
  if (i.reserved_for) return <StatusBadge status="waiting" label={`Giữ cho ${i.reserved_for.name ?? `#${i.reserved_for.display_id}`}`} />;
  return <StatusBadge status="match" label="Còn trống" />;
}

const origin = (i: StockItem) =>
  i.status === "returned"
    ? `Thu hồi${i.returned_from ? ` từ ${i.returned_from}` : ""}${i.returned_at ? ` · ${formatDate(i.returned_at)}` : ""}`
    : i.status === "new"
      ? "Hàng mới"
      : "";

/** Assets in stock: filters in the URL, multi-select bulk bar, SDP state changes as worker jobs. */
function AssetsTab({ d }: { d: Stock }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const view = ((params.get("status") as View) || "free") as View | "missing";
  const type = params.get("type") ?? "";
  const [search, setSearch] = useState(params.get("q") ?? "");
  const [picked, setPicked] = useState<string[]>([]);
  const [confirm, setConfirm] = useState<"pending" | "disposed" | null>(null);
  // asset id → job id of an SDP change started here; shown until the next sync refreshes the list.
  const [jobs, setJobs] = useState<Record<string, string>>({});
  const setParam = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
    setPicked([]);
  };
  useEffect(() => {
    const t = window.setTimeout(() => search !== (params.get("q") ?? "") && setParam("q", search), 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);
  useEffect(() => setJobs({}), [d.as_of]); // a new sync shows the real SDP state
  const jobIds = [...new Set(Object.values(jobs))];
  const jobStates = useQueries({
    queries: jobIds.map((id) => ({
      queryKey: ["jobs", id],
      queryFn: () => api<{ status: string }>(`/api/core/jobs/${id}`),
      refetchInterval: (q: { state: { data?: { status: string } } }) =>
        ["completed", "failed", "cancelled"].includes(q.state.data?.status ?? "") ? false : 3000,
    })),
  });
  const jobStatus = Object.fromEntries(jobIds.map((id, k) => [id, jobStates[k]?.data?.status]));
  const updating = (i: StockItem): "running" | "sync" | undefined => {
    const st = jobs[i.id] && jobStatus[jobs[i.id]!];
    if (!jobs[i.id]) return undefined;
    return st === "completed" ? "sync" : st === "failed" || st === "cancelled" ? undefined : "running";
  };

  const types = useMemo(() => [...new Set(d.items.map((i) => i.product_type ?? "Khác"))].sort(), [d.items]);
  const test = view === "missing" ? MISSING : (VIEWS.find((x) => x.v === view) ?? VIEWS[0]!).test;
  const needle = (params.get("q") ?? "").trim().toLowerCase();
  const shown = d.items.filter(
    (i) =>
      test(i) &&
      (!type || (i.product_type ?? "Khác") === type) &&
      (!needle || [i.asset_tag, i.serial, i.name, i.product, i.reserved_for?.name].some((x) => x?.toLowerCase().includes(needle))),
  );
  const selected = d.items.filter((i) => picked.includes(i.id));
  const shownIds = shown.map((i) => i.id);
  const allOn = shownIds.length > 0 && shownIds.every((id) => picked.includes(id));
  const canPick = d.can.manage || d.can.dispose;
  useEffect(() => {
    if (picked.length) announce(`Đã chọn ${picked.length} tài sản.`);
  }, [picked.length]);
  const toggle = (id: string) => setPicked((p) => (p.includes(id) ? p.filter((x) => x !== id) : [...p, id]));

  const mark = useMutation({
    mutationFn: (returned: boolean) => api("/api/lifecycle/stock/returned", { method: "POST", json: { asset_ids: picked, returned } }),
    onSuccess: async (_r, returned) => {
      toast.success(returned ? `Đã đánh dấu ${picked.length} tài sản là hàng thu hồi.` : `Đã chuyển ${picked.length} tài sản về hàng mới.`);
      setPicked([]);
      await qc.invalidateQueries({ queryKey: STOCK_KEY });
      await qc.invalidateQueries({ queryKey: ALLOC_KEY });
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
      const what = to === "disposed" ? "Thanh lý" : to === "pending" ? "Chuyển sang Chờ thanh lý" : "Trả về kho";
      setJobs((j) => ({ ...j, ...Object.fromEntries(picked.map((id) => [id, r.job_id])) }));
      toast.success(`${what}: đang ghi ${selected.length} tài sản lên SDP.`, {
        action: { label: "Xem Job", onClick: () => navigate(`/lifecycle/jobs/${r.job_id}`) },
      });
      setPicked([]);
      setConfirm(null);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const box = (i: StockItem) => (
    <input
      type="checkbox"
      className="size-4 accent-[var(--primary)]"
      aria-label={`Chọn ${i.asset_tag ?? i.name ?? i.id}`}
      checked={picked.includes(i.id)}
      disabled={!!updating(i)}
      onChange={() => toggle(i.id)}
    />
  );
  const missing = (label: string) => <StatusBadge status="failed" label={`Thiếu ${label}`} />;
  const columns = useMemo<ColumnDef<StockItem, unknown>[]>(
    () => [
      ...(canPick
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
              cell: (c) => box(c.row.original),
            } as ColumnDef<StockItem, unknown>,
          ]
        : []),
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
      {
        id: "availability",
        header: "Tình trạng",
        enableSorting: false,
        cell: (c) => (
          <span className="block">
            <Availability i={c.row.original} updating={updating(c.row.original)} />
            {origin(c.row.original) && <span className="mt-0.5 block text-xs text-muted">{origin(c.row.original)}</span>}
          </span>
        ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [canPick, picked, allOn, shownIds.join(), jobs, JSON.stringify(jobStatus)],
  );

  const reservedPicked = selected.filter((i) => i.reserved_for);
  const tags = (list: StockItem[]) => `${list.slice(0, 8).map((i) => i.asset_tag ?? i.name ?? i.id).join(", ")}${list.length > 8 ? ` và ${list.length - 8} tài sản khác` : ""}`;
  const count = (t: (i: StockItem) => boolean) => d.items.filter(t).length;

  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label="Lọc theo tình trạng">
        {VIEWS.map((t) => (
          <button
            key={t.v}
            type="button"
            aria-pressed={view === t.v}
            onClick={() => setParam("status", t.v === "free" ? "" : t.v)}
            className={cn(
              "h-8 cursor-pointer rounded-full border px-3 text-sm transition-colors duration-150",
              view === t.v ? "border-primary bg-primary-soft font-medium text-primary" : "border-line-strong bg-surface hover:bg-surface-2",
            )}
          >
            {t.label} <span className="tabular">({count(t.test)})</span>
          </button>
        ))}
        {count(MISSING) > 0 && (
          <button
            type="button"
            aria-pressed={view === "missing"}
            onClick={() => setParam("status", "missing")}
            className={cn(
              "h-8 cursor-pointer rounded-full border px-3 text-sm",
              view === "missing" ? "border-failed bg-failed-soft font-medium text-failed" : "border-failed text-failed hover:bg-failed-soft",
            )}
          >
            Thiếu mã TS / serial <span className="tabular">({count(MISSING)})</span>
          </button>
        )}
      </div>
      <form role="search" aria-label="Lọc tài sản" className="mb-3 grid gap-3 sm:grid-cols-[2fr_1fr]" onSubmit={(e) => e.preventDefault()}>
        <Field label="Tìm mã tài sản, serial, tên hoặc người được giữ">
          <Input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Ví dụ: HCM-2026-LT-1234 hoặc 5CD43…" />
        </Field>
        <Field label="Loại tài sản">
          <Select value={type} onChange={(e) => setParam("type", e.target.value)}>
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
            <Button size="sm" onClick={() => setConfirm("pending")}>
              <PackageX className="size-4" aria-hidden="true" />Chuyển Chờ thanh lý…
            </Button>
          )}
          {d.can.manage && selected.every((i) => i.status === "pending") && (
            <Button size="sm" onClick={() => change.mutate("in_store")} loading={change.isPending}>
              <PackageCheck className="size-4" aria-hidden="true" />Trả về kho
            </Button>
          )}
          {d.can.dispose && selected.every((i) => i.status === "pending") && (
            <Button size="sm" variant="danger" onClick={() => setConfirm("disposed")}>
              <Archive className="size-4" aria-hidden="true" />Thanh lý trên SDP…
            </Button>
          )}
          <Button size="sm" variant="ghost" onClick={() => setPicked([])}>Bỏ chọn</Button>
          {selected.some((i) => i.status === "pending") && selected.some((i) => i.status !== "pending") && (
            <span className="basis-full text-muted">Chọn riêng hàng Chờ thanh lý để thanh lý hoặc trả về kho.</span>
          )}
        </div>
      )}
      {/* Phones: one card per asset (status visible without scrolling sideways). */}
      <ul className="space-y-2 sm:hidden" aria-label="Danh sách tài sản">
        {shown.length === 0 && <li className="py-6 text-center text-sm text-muted">Không có tài sản phù hợp.</li>}
        {shown.map((i) => (
          <li key={i.id} className="flex gap-3 rounded-md border border-line bg-surface p-3">
            {canPick && <span className="pt-0.5">{box(i)}</span>}
            <span className="min-w-0 flex-1 space-y-1 text-sm">
              <span className="block font-medium">{i.name ?? i.id}</span>
              <span className="flex flex-wrap gap-1.5">
                {i.asset_tag ? <Mono>{i.asset_tag}</Mono> : missing("mã TS")}
                {i.serial ? <Mono>{i.serial}</Mono> : missing("serial")}
              </span>
              <span className="block text-muted">{i.product_type ?? "—"}{i.product ? ` · ${i.product}` : ""}</span>
              <Availability i={i} updating={updating(i)} />
              {origin(i) && <span className="block text-xs text-muted">{origin(i)}</span>}
            </span>
          </li>
        ))}
      </ul>
      <div className="hidden sm:block">
        <DataTable
          data={shown}
          columns={columns}
          caption="Tài sản trong kho"
          getRowId={(r) => r.id}
          empty={
            <EmptyState icon={Archive} title={d.items.length ? "Không có tài sản phù hợp" : "Kho trống"}>
              {d.items.length ? "Đổi bộ lọc tình trạng hoặc loại để xem tài sản khác." : "Không có tài sản nào chưa gán người dùng."}
            </EmptyState>
          }
        />
      </div>
      <Dialog
        open={confirm === "pending"}
        onClose={() => setConfirm(null)}
        title={`Chuyển ${selected.length} tài sản sang Chờ thanh lý`}
        description={`Đổi trạng thái trên ServiceDesk Plus thành “${d.states.pending}” bằng API key của bạn. Có thể trả về kho sau.`}
        footer={
          <>
            <Button onClick={() => setConfirm(null)}>Huỷ</Button>
            <Button variant="primary" loading={change.isPending} onClick={() => change.mutate("pending")}>Chuyển Chờ thanh lý</Button>
          </>
        }
      >
        <p className="font-mono text-sm">{tags(selected)}</p>
        {reservedPicked.length > 0 && (
          <p role="alert" className="mt-3 rounded-sm bg-waiting-soft px-3 py-2 text-sm text-waiting">
            {reservedPicked.length} tài sản đang giữ cho nhân viên sắp onboard ({tags(reservedPicked)}). Chuyển đi thì hệ thống sẽ gợi ý tài sản khác hoặc đề xuất mua.
          </p>
        )}
      </Dialog>
      <ConfirmDangerDialog
        open={confirm === "disposed"}
        onClose={() => setConfirm(null)}
        onConfirm={() => change.mutate("disposed")}
        loading={change.isPending}
        title={`Thanh lý ${selected.length} tài sản`}
        consequences={
          <>
            <span className="block">Đổi trạng thái trên ServiceDesk Plus thành “{d.states.disposed}”; tài sản rời khỏi kho. Ghi bằng API key của bạn và được lưu nhật ký.</span>
            <span className="mt-1 block font-mono text-sm">{tags(selected)}</span>
          </>
        }
        confirmText="THANH LY"
        inputLabel={<>Nhập <strong className="font-mono">THANH LY</strong> để xác nhận</>}
        confirmLabel="Thanh lý"
      />
    </>
  );
}

/** Kho thiết bị: what new hires need from stock, and every asset in stock (one page, one vocabulary). */
export function StockPage() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const alloc = useQuery({ queryKey: ALLOC_KEY, queryFn: () => api<Allocation>("/api/lifecycle/allocation") });
  const stock = useQuery({ queryKey: STOCK_KEY, queryFn: () => api<Stock>("/api/lifecycle/stock") });
  const purchases = useQuery({
    queryKey: ["workload", "purchases", "onboard"],
    queryFn: () => api<{ items: PurchaseLite[] }>("/api/workload/purchases"),
    enabled: can("workload.tickets.view"),
  });
  const a = alloc.data;
  const s = stock.data;
  const needAction = (a?.requests ?? []).filter((r) => r.status !== "covered").length;
  // Default: coordinators with nothing to prepare land on the assets; everyone else on the people to equip.
  const tab = params.get("tab") ?? (s?.can.manage && a && needAction === 0 ? "assets" : "onboard");
  const go = (next: Record<string, string>) => {
    const p = new URLSearchParams();
    for (const [k, v] of Object.entries(next)) if (v) p.set(k, v);
    setParams(p, { replace: true });
  };
  const pendingOf = (types: string[]) => (s?.items ?? []).filter((i) => i.status === "pending" && types.includes(i.product_type ?? "")).length;
  const tiles = (a?.stock ?? []).map((l) => ({ ...l, pending: pendingOf(l.product_types), free: Math.max(0, l.in_store - l.planned) }));
  const live = tiles.filter((t) => t.standard && (t.in_store || t.to_buy || t.pending));
  const empty = tiles.filter((t) => t.standard && !t.in_store && !t.to_buy && !t.pending);
  const other = tiles.filter((t) => !t.standard);
  const error = alloc.error ?? stock.error;

  return (
    <>
      <PageHeader
        title="Kho thiết bị"
        description={
          <>
            Chuẩn bị thiết bị cho nhân viên mới từ kho trên ServiceDesk Plus, thiếu thì đề xuất mua; quản lý hàng mới, hàng thu hồi và hàng chờ thanh lý.{" "}
            <HelpTip title="Kho thiết bị">
              <span className="block"><b>Gợi ý cho nhân viên mới:</b> lấy band trong request RTR (hoặc nhập tay), tra tiêu chuẩn cấp phát, rồi lấy tài sản còn trống đúng loại – <b>hàng thu hồi trước, hàng mới sau</b>; ai bắt đầu sớm hơn được ưu tiên; tài sản đã chọn trên Job nháp giữ cho Job đó. Không đủ → Cần mua → Lập đề xuất mua.</span>
              <span className="block"><b>Tình trạng tài sản:</b> Còn trống, Giữ cho (một nhân viên sắp onboard), Chờ thanh lý (“{s?.states.pending ?? "Chờ thanh lý"}” trên SDP). Hàng thu hồi do đồng bộ tự nhận ra hoặc đánh dấu tay. Thanh lý đổi sang “{s?.states.disposed ?? "Disposed"}” – chỉ Manager và Administrator.</span>
              <span className="block">Thay đổi trên SDP chạy bằng API key của chính bạn (Khoá API của tôi). Mã tài sản và serial là bắt buộc – tài sản thiếu được đánh dấu đỏ.</span>
            </HelpTip>
          </>
        }
      />
      {error && <ErrorState error={error} onRetry={() => void Promise.all([alloc.refetch(), stock.refetch()])} />}
      {!a || !s ? (
        !error && <LoadingRows rows={8} />
      ) : !s.as_of ? (
        <Panel>
          <EmptyState icon={Archive} title="Chưa có dữ liệu kho">
            Chạy đồng bộ ở trang <Link className="text-primary underline" to="/lifecycle/sync">Đồng bộ dữ liệu</Link> để đọc tài sản từ ServiceDesk Plus.
          </EmptyState>
        </Panel>
      ) : (
        <>
          <p className="mb-3 text-sm text-muted">Theo dữ liệu đồng bộ lúc {formatDateTime(s.as_of)}.</p>
          <ul aria-label="Kho theo loại thiết bị" className="mb-2 grid gap-2 sm:grid-cols-2 lg:grid-cols-4">
            {live.map((t) => (
              <li key={t.label}>
                <button
                  type="button"
                  onClick={() => go({ tab: "assets", type: t.product_types[0] ?? "", status: "all" })}
                  className="block w-full cursor-pointer rounded-md border border-line bg-surface p-3 text-left shadow-card hover:bg-surface-2"
                >
                  <span className="block font-semibold">{t.label}</span>
                  <dl className="mt-1 grid grid-cols-3 gap-1 text-sm">
                    <div><dt className="text-xs text-muted">Còn trống</dt><dd className="text-lg font-semibold tabular">{t.free}</dd></div>
                    <div><dt className="text-xs text-muted">Đang giữ</dt><dd className="text-lg font-semibold tabular">{t.planned}</dd></div>
                    <div><dt className="text-xs text-muted">Cần mua</dt><dd className={cn("text-lg font-semibold tabular", t.to_buy > 0 && "text-waiting")}>{t.to_buy}</dd></div>
                  </dl>
                  {t.pending > 0 && <span className="mt-1 block text-xs text-muted">+ {t.pending} chờ thanh lý</span>}
                </button>
              </li>
            ))}
          </ul>
          {(empty.length > 0 || other.length > 0) && (
            <p className="mb-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted">
              {empty.length > 0 && <span>Không có tồn và không cần: {empty.map((t) => t.label).join(", ")}.</span>}
              {other.length > 0 && (
                <span className="flex flex-wrap items-center gap-1.5">
                  Ngoài tiêu chuẩn:
                  {other.map((t) => (
                    <button
                      key={t.label}
                      type="button"
                      onClick={() => go({ tab: "assets", type: t.product_types[0] ?? "", status: "all" })}
                      className="cursor-pointer rounded-full border border-line-strong px-2 py-0.5 text-ink hover:bg-surface-2"
                    >
                      {t.label} <span className="tabular">{t.in_store}</span>
                    </button>
                  ))}
                </span>
              )}
            </p>
          )}
          <Tabs
            label="Kho thiết bị"
            value={tab}
            onChange={(k) => go({ tab: k })}
            tabs={[
              { key: "onboard", label: <>Nhân viên mới {needAction > 0 ? <span className="text-waiting">({needAction} cần xử lý)</span> : `(${a.requests.length})`}</> },
              { key: "assets", label: `Tài sản trong kho (${s.items.length})` },
            ]}
          >
            <div className="pt-4">{tab === "assets" ? <AssetsTab d={s} /> : <OnboardTab d={a} purchases={purchases.data?.items ?? null} />}</div>
          </Tabs>
        </>
      )}
    </>
  );
}
