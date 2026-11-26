import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { CheckCheck, ChevronDown, Download, FilterX, GitCompareArrows, Play, RefreshCw, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button, buttonVariants } from "@/core/ui/button";
import { DataTable, type Density } from "@/core/ui/data-table";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input, Select, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { Menu, MenuItem } from "@/core/ui/menu";
import { StatusBadge } from "@/core/ui/status";
import { formatDate, formatRelative } from "@/core/lib/format";
import { chip } from "../chip";
import { useDebounced } from "@/core/lib/useDebounced";
import { useRules, useSyncNow, useSyncStatus, type ReconResponse, type ReconRow } from "../api";
import { CellChip } from "../CellChip";
import { KIND_LABEL, NOTE_TITLE, SEVERITY, SOURCE_LABEL } from "../labels";
import { EmployeePanel } from "./EmployeePanel";

const FILTER_KEYS = ["q", "severity", "rule", "kind", "department", "only_issues"] as const;
const WIDE = "(min-width: 1280px)";

/** The side panel sits next to the table only on wide screens; below that it opens as a full-screen sheet. */
function useWide(): boolean {
  return useSyncExternalStore(
    (cb) => {
      const m = window.matchMedia(WIDE);
      m.addEventListener("change", cb);
      return () => m.removeEventListener("change", cb);
    },
    () => window.matchMedia(WIDE).matches,
  );
}

/** Full-screen sheet for the employee panel on phones and tablets (native <dialog>: focus trap, Esc, inert page). */
function PanelSheet({ children, onClose }: { children: React.ReactNode; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    if (ref.current && !ref.current.open) ref.current.showModal();
  }, []);
  return (
    <dialog ref={ref} onClose={onClose} aria-label="Chi tiết nhân viên" className="m-0 h-dvh max-h-none w-full max-w-none bg-surface p-0 text-ink">
      {children}
    </dialog>
  );
}

function SyncBanner({ data }: { data: ReconResponse }) {
  const run = data.run!;
  const failed = Object.keys(run.errors ?? {});
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
      <span>
        Dữ liệu lúc <strong className="font-medium text-ink">{formatRelative(run.finished_at)}</strong> ·{" "}
        {run.stats.employees ?? 0} nhân viên · {run.stats.requests ?? 0} request RTR · {run.stats.assets ?? 0} tài sản ·{" "}
        {run.stats.biostar_users ?? 0} user BioStar
        {run.stats.biostar_retired ? ` (ẩn ${run.stats.biostar_retired} user nhóm loại trừ)` : ""}
      </span>
      {failed.length > 0 && (
        <Link to="/lifecycle/sync" className="inline-flex items-center gap-1 text-waiting hover:underline">
          <TriangleAlert className="size-4" aria-hidden="true" />
          Không đọc được: {failed.map((f) => SOURCE_LABEL[f] ?? f).join(", ")} – các quy tắc liên quan đã bỏ qua
        </Link>
      )}
    </div>
  );
}

/** Acknowledge the open mismatches of several employees at once (one rule when the list is filtered on it). */
function BulkAckDialog({ rows, rule, ruleTitle, onClose, onDone }: { rows: ReconRow[]; rule: string; ruleTitle?: string; onClose: () => void; onDone: () => void }) {
  const qc = useQueryClient();
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const issues = rows.flatMap((r) => r.issues.filter((i) => !i.acked && (!rule || i.rule === rule)).map((i) => ({ emp: r.employee_id, rule: i.rule })));
  const run = async () => {
    setBusy(true);
    let failed = 0;
    for (const i of issues) {
      try {
        await api(`/api/lifecycle/reconciliation/${encodeURIComponent(i.emp)}/ack`, { method: "POST", json: { rule: i.rule, note } });
      } catch {
        failed += 1;
      }
    }
    await qc.invalidateQueries({ queryKey: ["lifecycle", "recon"] });
    setBusy(false);
    if (failed) toast.error(`Đã đánh dấu ${issues.length - failed}/${issues.length} lệch; ${failed} lệch không lưu được – thử lại.`);
    else toast.success(`Đã đánh dấu ${issues.length} lệch là đã biết.`);
    onDone();
  };
  return (
    <Dialog
      open
      onClose={onClose}
      title="Đánh dấu đã biết"
      description={`${issues.length} lệch của ${rows.length} nhân viên${rule ? ` – chỉ loại “${ruleTitle ?? rule}”` : ""}. Lệch đã biết không tính vào số lệch cho tới khi bấm “Mở lại”.`}
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" loading={busy} disabled={!issues.length} onClick={run}>Đánh dấu {issues.length} lệch</Button>
        </>
      }
    >
      <Field label="Ghi chú (không bắt buộc)" hint="Ghi cho tất cả lệch được chọn, ví dụ: đã báo bảo vệ thu thẻ.">
        <Textarea rows={3} value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} data-autofocus />
      </Field>
    </Dialog>
  );
}

export function ReconciliationPage() {
  const { can } = useAuth();
  const connections = useQuery({
    queryKey: ["connections"],
    queryFn: () => api<unknown[]>("/api/core/connections"),
    enabled: can("core.connections.view"),
  });
  const noConnections = connections.data?.length === 0;
  const [params, setParams] = useSearchParams();
  const [selected, setSelectedState] = useState<string | null>(params.get("emp"));
  const [picked, setPicked] = useState<string[]>([]);
  const [order, setOrder] = useState<string[]>([]); // row ids as the table shows them (after sorting)
  const [ackOpen, setAckOpen] = useState(false);
  const [creating, setCreating] = useState(false);
  const qc = useQueryClient();
  const canBulk = can("lifecycle.reconciliation.sync");
  const wide = useWide();
  // Remember which row opened the panel so closing it hands focus back there.
  const opener = useRef<HTMLElement | null>(null);
  const setSelected = (emp: string | null) => {
    if (emp && !selected) opener.current = document.activeElement as HTMLElement | null;
    setSelectedState(emp);
    if (!emp) {
      const back = opener.current;
      window.setTimeout(() => (back?.isConnected ? back : document.querySelector<HTMLElement>(`[data-emp="${selected}"]`))?.focus(), 0);
    }
  };
  const [density, setDensity] = useState<Density>("compact");
  const [search, setSearch] = useState(params.get("q") ?? "");
  const rules = useRules();
  const syncStatus = useSyncStatus();
  const syncNow = useSyncNow();
  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? ""])) as Record<(typeof FILTER_KEYS)[number], string>;
  // The work list is the default: rows with mismatches. "Tất cả" sets only_issues=false explicitly.
  if (!filters.only_issues) filters.only_issues = "true";
  const narrowed = ["q", "rule", "kind", "department"].some((k) => filters[k as keyof typeof filters]);
  const clearFilters = () => {
    setSearch("");
    const next = new URLSearchParams(params);
    for (const k of ["q", "rule", "kind", "department", "severity"]) next.delete(k);
    setParams(next, { replace: true });
  };

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const debouncedSearch = useDebounced(search);
  useEffect(() => {
    if (debouncedSearch !== filters.q) setFilter("q", debouncedSearch);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
  // A new filter is a new list: drop the selection rather than act on rows no longer shown.
  useEffect(() => setPicked([]), [qs.toString()]);
  const q = useQuery({
    queryKey: ["lifecycle", "recon", filters],
    queryFn: () => api<ReconResponse>(`/api/lifecycle/reconciliation?${qs}&limit=1000`),
  });

  // Refresh when a sync finishes (not on the first value seen: the list was just loaded).
  const lastFinished = syncStatus.data?.task?.last_finished_at;
  const seenFinished = useRef<string | null | undefined>(undefined);
  useEffect(() => {
    if (seenFinished.current !== undefined && lastFinished) void q.refetch();
    if (syncStatus.data) seenFinished.current = lastFinished ?? null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastFinished]);

  const ruleTitle = useMemo(() => Object.fromEntries((rules.data ?? []).map((r) => [r.code, r.title])), [rules.data]);
  const pickable = (q.data?.items ?? []).filter((r) => r.issue_count > 0).map((r) => r.employee_id);
  const allPicked = pickable.length > 0 && pickable.every((id) => picked.includes(id));

  const columns = useMemo<ColumnDef<ReconRow, unknown>[]>(() => {
    const flagged = (row: ReconRow, system: string) => row.issues.some((i) => !i.acked && i.system === system);
    const select: ColumnDef<ReconRow, unknown>[] = canBulk
      ? [
          {
            id: "select",
            enableSorting: false,
            header: () => (
              <input
                type="checkbox"
                className="size-4 accent-[var(--primary)]"
                aria-label="Chọn tất cả nhân viên đang lệch"
                checked={allPicked}
                disabled={!pickable.length}
                onChange={() => setPicked(allPicked ? [] : pickable)}
              />
            ),
            cell: (c) =>
              c.row.original.issue_count > 0 ? (
                <input
                  type="checkbox"
                  className="size-4 accent-[var(--primary)]"
                  aria-label={`Chọn ${c.row.original.employee_id}`}
                  checked={picked.includes(c.row.original.employee_id)}
                  onClick={(e) => e.stopPropagation()}
                  onChange={() => setPicked((p) => (p.includes(c.row.original.employee_id) ? p.filter((x) => x !== c.row.original.employee_id) : [...p, c.row.original.employee_id]))}
                />
              ) : null,
          },
        ]
      : [];
    return [
      ...select,
      {
        accessorKey: "employee_id",
        header: "Mã NV",
        cell: (c) => (
          <button type="button" data-emp={c.row.original.employee_id} className="cursor-pointer font-mono text-[0.8125rem] hover:text-primary hover:underline" onClick={() => setSelected(c.row.original.employee_id)}>
            {c.row.original.employee_id}
          </button>
        ),
      },
      { accessorKey: "full_name", header: "Họ tên", cell: (c) => <span className="block max-w-48 truncate font-medium">{c.row.original.full_name || "—"}</span> },
      { accessorKey: "department", header: "Phòng ban", cell: (c) => <span className="block max-w-40 truncate text-muted">{c.row.original.department ?? "—"}</span> },
      {
        accessorKey: "effective_date",
        header: "Biến động",
        cell: (c) =>
          c.row.original.kind ? (
            <span className="whitespace-nowrap">
              {KIND_LABEL[c.row.original.kind]}
              <span className="block text-xs text-muted tabular">{formatDate(c.row.original.effective_date)}</span>
            </span>
          ) : (
            <span className="text-muted">—</span>
          ),
      },
      { id: "sdp", header: "SDP", enableSorting: false, cell: (c) => <CellChip system="SDP" cell={c.row.original.cells.sdp} flagged={flagged(c.row.original, "sdp")} /> },
      { id: "assets", header: "Tài sản", enableSorting: false, cell: (c) => <CellChip system="Tài sản" cell={c.row.original.cells.assets} flagged={flagged(c.row.original, "assets")} /> },
      { id: "biostar", header: "BioStar", enableSorting: false, cell: (c) => <CellChip system="BioStar" cell={c.row.original.cells.biostar} flagged={flagged(c.row.original, "biostar")} /> },
      {
        accessorKey: "issue_count",
        header: "Lệch",
        cell: (c) => {
          const r = c.row.original;
          if (!r.issue_count) return <StatusBadge status="match" label="Khớp" />;
          const reasons = r.issues.filter((i) => !i.acked).map((i) => ruleTitle[i.rule] ?? i.rule);
          return (
            <span title={reasons.join("\n")}>
              <StatusBadge status={SEVERITY[r.max_severity!].status} label={`${r.issue_count} · ${SEVERITY[r.max_severity!].short}`} />
              <span className="sr-only">: {reasons.join("; ")}</span>
            </span>
          );
        },
      },
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ruleTitle, canBulk, picked, allPicked, pickable.join()]);
  // With the side panel open, keep the system columns in view: Phòng ban and Biến động are in the panel.
  const shownColumns = useMemo(
    () => (selected && wide ? columns.filter((c) => !["department", "effective_date"].includes((c as { accessorKey?: string }).accessorKey ?? "")) : columns),
    [columns, selected, wide],
  );

  const download = async (format: "xlsx" | "csv") => {
    try {
      const res = await fetch(`/api/lifecycle/reconciliation/export?${qs}&format=${format}`, { credentials: "same-origin" });
      if (!res.ok) throw new Error("Không xuất được file. Thử lại sau.");
      const blob = await res.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? `doi-soat.${format}`;
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };

  const createJobs = async () => {
    const rows = (q.data?.items ?? []).filter((r) => picked.includes(r.employee_id));
    // Only each employee's latest request: an older one (e.g. the onboard of someone now leaving) is not open work.
    const reqs = rows.flatMap((r) => ((r.kind === "onboard" || r.kind === "offboard") && r.latest_request_id ? [r.latest_request_id] : []));
    setCreating(true);
    let ok = 0;
    for (const id of reqs) {
      try {
        await api("/api/lifecycle/jobs", { method: "POST", json: { request_id: id } });
        ok += 1;
      } catch {
        // request of another kind or with parse errors: reported below
      }
    }
    setCreating(false);
    await qc.invalidateQueries({ queryKey: ["jobs"] });
    const skipped = rows.length - reqs.length;
    toast.success(
      `Đã tạo hoặc mở ${ok} Job nháp${skipped ? `; bỏ qua ${skipped} nhân viên có request mới nhất không phải Nhân viên mới / Nghỉ việc` : ""}${reqs.length - ok ? `; ${reqs.length - ok} request chưa tạo được (thiếu thông tin)` : ""}. Xem ở trang Job.`,
    );
    setPicked([]);
  };

  const data = q.data;
  const unknown = (data?.notes ?? []).filter((n) => n.rule === "biostar_unknown_employee");
  const unknownActive = unknown.filter((n) => n.active).length;
  const general = (data?.notes ?? []).filter((n) => n.rule !== "biostar_unknown_employee");
  // Previous / next follow the table as shown, so a sort by name walks names in order.
  const at = selected ? order.indexOf(selected) : -1;
  const step =
    at < 0
      ? undefined
      : {
          prev: at > 0 ? () => setSelectedState(order[at - 1]!) : undefined,
          next: at < order.length - 1 ? () => setSelectedState(order[at + 1]!) : undefined,
        };
  const sev = data?.facets.severity ?? {};
  const syncing = syncNow.isPending || syncStatus.data?.task?.running;

  return (
    <>
      <PageHeader
        title="Đối soát theo Mã NV"
        description="Mỗi dòng là một nhân viên: request RTR, tài sản trên ServiceDesk Plus và user BioStar. Hệ thống đang lệch có biểu tượng cảnh báo và chữ “Lệch”."
        actions={
          <>
            {can("lifecycle.reconciliation.sync") && (
              <Button onClick={() => syncNow.mutate()} loading={!!syncing}>
                {!syncing && <RefreshCw className="size-4" aria-hidden="true" />}
                {syncing ? "Đang đồng bộ…" : "Đồng bộ ngay"}
              </Button>
            )}
            {/* Nothing to export before the first sync, so the action is not offered at all. */}
            {data?.run && <Menu
              label="Xuất dữ liệu"
              trigger={(p) => (
                <Button {...p}>
                  <Download className="size-4" aria-hidden="true" />
                  Xuất danh sách
                  <ChevronDown className="size-3.5" aria-hidden="true" />
                </Button>
              )}
            >
              <MenuItem onSelect={() => download("xlsx")}>Excel (.xlsx) – theo bộ lọc hiện tại</MenuItem>
              <MenuItem onSelect={() => download("csv")}>CSV (.csv) – theo bộ lọc hiện tại</MenuItem>
            </Menu>}
          </>
        }
      />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading && <div className="rounded-md border border-line bg-surface"><LoadingRows rows={10} label="Đang tải dữ liệu đối soát" /></div>}
      {data && !data.run && (
        <div className="rounded-md border border-line bg-surface">
          <EmptyState
            icon={GitCompareArrows}
            title="Chưa có dữ liệu đối soát"
            action={
              noConnections ? (
                can("core.connections.manage") && (
                  <Link to="/settings/connections" className={buttonVariants({ variant: "primary" })}>Thêm kết nối</Link>
                )
              ) : (
                can("lifecycle.reconciliation.sync") && <Button variant="primary" onClick={() => syncNow.mutate()} loading={!!syncing}>Đồng bộ lần đầu</Button>
              )
            }
          >
            {noConnections ? (
              <>Chưa có kết nối tới ServiceDesk Plus và BioStar. {can("core.connections.manage") ? "Thêm kết nối với credential Read rồi quay lại đồng bộ." : "Nhờ Quản trị thêm kết nối."}</>
            ) : (
              <>
                Chạy đồng bộ để đọc request, user, tài sản từ SDP và user BioStar. Xem trạng thái ở trang{" "}
                <Link className="text-primary underline" to="/lifecycle/sync">Đồng bộ dữ liệu</Link>.
              </>
            )}
          </EmptyState>
        </div>
      )}
      {data?.run && (
        <>
          <SyncBanner data={data} />
          <div className="mb-3 flex flex-wrap items-center gap-2" role="group" aria-label="Lọc theo mức độ">
            {([
              ["issues", `Có lệch (${(sev.high ?? 0) + (sev.medium ?? 0) + (sev.low ?? 0)})`, !filters.severity && filters.only_issues === "true"],
              ["high", `${SEVERITY.high.label} (${sev.high ?? 0})`, filters.severity === "high"],
              ["medium", `${SEVERITY.medium.label} (${sev.medium ?? 0})`, filters.severity === "medium"],
              ["low", `${SEVERITY.low.label} (${sev.low ?? 0})`, filters.severity === "low"],
              ["all", `Tất cả (${data.facets.total_rows ?? 0})`, !filters.severity && filters.only_issues === "false"],
            ] as const).map(([value, label, pressed]) => (
              <button
                key={value}
                type="button"
                aria-pressed={pressed}
                onClick={() => {
                  const next = new URLSearchParams(params);
                  next.delete("severity");
                  next.delete("only_issues");
                  if (value === "all") next.set("only_issues", "false");
                  else if (value !== "issues") next.set("severity", value);
                  setParams(next, { replace: true });
                }}
                className={chip(pressed, true)}
              >
                {label}
              </button>
            ))}
            <HelpTip title="Mức độ và “Đánh dấu đã biết”">
              <span className="block">Cao: rủi ro an ninh hoặc tài sản (vd. đã nghỉ việc nhưng BioStar còn hoạt động, còn tài sản đang gán). Trung bình: thiếu hoặc sai dữ liệu giữa các hệ thống. Thấp: cần xem lại (tên lệch, request chưa đủ thông tin).</span>
              <span className="block">Mở một dòng để xem lệch và xử lý qua Job onboard/offboard; trong panel, phím j / k sang nhân viên sau / trước. “Đánh dấu đã biết” bỏ lệch đó khỏi số đếm, giữ qua các lần đồng bộ cho tới khi bấm “Mở lại”.</span>
              <span className="block">Chọn nhiều dòng bằng ô đánh dấu để đánh dấu đã biết hoặc tạo Job nháp cùng lúc; đang lọc theo một loại lệch thì chỉ đánh dấu loại đó.</span>
            </HelpTip>
          </div>
          <form role="search" aria-label="Lọc đối soát" className="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-[2fr_1fr_1.5fr_1.5fr_auto]" onSubmit={(e) => e.preventDefault()}>
            <Field label="Tìm Mã NV hoặc họ tên">
              <Input type="search" value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Ví dụ: 003551 hoặc nguyen van an" />
            </Field>
            <Field label="Biến động">
              <Select value={filters.kind} onChange={(e) => setFilter("kind", e.target.value)}>
                <option value="">Tất cả</option>
                {Object.entries(KIND_LABEL).filter(([k]) => k !== "ignore").map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </Field>
            <Field label="Loại lệch">
              <Select value={filters.rule} onChange={(e) => setFilter("rule", e.target.value)}>
                <option value="">Tất cả</option>
                {(rules.data ?? [])
                  .filter((r) => (data.facets.rules?.[r.code] ?? 0) > 0 || r.code === filters.rule)
                  .map((r) => <option key={r.code} value={r.code}>{r.title} ({data.facets.rules?.[r.code] ?? 0})</option>)}
              </Select>
            </Field>
            <Field label="Phòng ban">
              <Select value={filters.department} onChange={(e) => setFilter("department", e.target.value)}>
                <option value="">Tất cả</option>
                {(data.facets.departments ?? []).map((d) => <option key={d} value={d}>{d}</option>)}
              </Select>
            </Field>
            <div className="flex items-end">
              <Button onClick={clearFilters} disabled={!narrowed && !filters.severity}>
                <FilterX className="size-4" aria-hidden="true" />
                Xoá bộ lọc
              </Button>
            </div>
          </form>
          {picked.length > 0 && (
            <div role="region" aria-label="Thao tác với nhân viên đã chọn" className="mb-3 flex flex-wrap items-center gap-3 rounded-md border border-primary bg-primary-soft px-3 py-2 text-sm">
              <span>Đã chọn <strong className="tabular">{picked.length}</strong> nhân viên</span>
              <Button size="sm" variant="primary" onClick={() => setAckOpen(true)}>
                <CheckCheck className="size-4" aria-hidden="true" />
                Đánh dấu đã biết…
              </Button>
              {can("lifecycle.job.execute") && (
                <Button size="sm" onClick={createJobs} loading={creating}>
                  <Play className="size-4" aria-hidden="true" />
                  Tạo Job onboard / offboard
                </Button>
              )}
              <Button size="sm" variant="ghost" onClick={() => setPicked([])}>Bỏ chọn</Button>
            </div>
          )}
          {ackOpen && (
            <BulkAckDialog
              rows={data.items.filter((r) => picked.includes(r.employee_id))}
              rule={filters.rule}
              ruleTitle={ruleTitle[filters.rule]}
              onClose={() => setAckOpen(false)}
              onDone={() => {
                setAckOpen(false);
                setPicked([]);
              }}
            />
          )}
          <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
            <p className="text-sm text-muted" role="status">{data.total} nhân viên phù hợp</p>
            <label className="flex cursor-pointer items-center gap-2 text-sm whitespace-nowrap">
              <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={density === "comfortable"} onChange={(e) => setDensity(e.target.checked ? "comfortable" : "compact")} />
              Dòng thoáng
            </label>
          </div>
          <div className={selected && wide ? "grid gap-4 xl:grid-cols-[minmax(0,1fr)_380px]" : undefined}>
            <DataTable
              data={data.items}
              columns={shownColumns}
              onOrderChange={setOrder}
              caption="Đối soát theo Mã nhân viên"
              getRowId={(r) => r.employee_id}
              onRowClick={(r) => setSelected(r.employee_id)}
              selectedId={selected}
              density={density}
              maxHeight="calc(100dvh - 330px)"
              empty={
                <EmptyState
                  icon={GitCompareArrows}
                  title={narrowed || filters.severity ? "Không có nhân viên phù hợp" : "Không còn lệch nào"}
                  action={(narrowed || filters.severity) && <Button onClick={clearFilters}><FilterX className="size-4" aria-hidden="true" />Xoá bộ lọc</Button>}
                >
                  {narrowed || filters.severity ? "Bộ lọc hiện tại không khớp nhân viên nào." : "Mọi nhân viên đã khớp giữa các hệ thống. Chọn “Tất cả” để xem toàn bộ."}
                </EmptyState>
              }
            />
            {selected && wide && <EmployeePanel employeeId={selected} onClose={() => setSelected(null)} onStep={step} />}
            {selected && !wide && (
              <PanelSheet onClose={() => setSelected(null)}>
                <EmployeePanel employeeId={selected} onClose={() => setSelected(null)} onStep={step} className="min-h-full bg-surface" />
              </PanelSheet>
            )}
          </div>
          {unknown.length > 0 && (
            <details className="mt-4 rounded-md border border-line bg-surface">
              <summary className="cursor-pointer px-4 py-2.5 font-medium">
                BioStar không có trên SDP ({unknown.length})
                {unknownActive > 0 && <span className="text-failed"> – {unknownActive} còn quyền ra vào</span>}
              </summary>
              <p className="border-t border-line px-4 py-2 text-sm text-muted">
                SDP là nguồn chính: các user BioStar này không khớp user SDP hay request RTR nào nên không nằm trong danh sách
                nhân viên. Rà soát quyền ra vào; nhà cung cấp, khách… thì thêm nhóm vào <b>Cấu hình module → Nhóm loại trừ khỏi đối soát</b>.
              </p>
              <ul className="divide-y divide-line border-t border-line">
                {unknown.map((n) => (
                  <li key={`${n.employee_id}-${n.source ?? ""}`} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-4 py-2 text-sm">
                    <Mono>{n.employee_id}</Mono>
                    <span className="font-medium">{n.name}</span>
                    {n.active ? <StatusBadge status="warning" label="Còn quyền ra vào" /> : <StatusBadge status="disabled" label="Đã khoá / hết hạn" />}
                    <span className="text-muted">
                      Nhóm: {n.group || "—"}
                      {n.source ? ` · ${n.source}` : ""}
                    </span>
                  </li>
                ))}
              </ul>
            </details>
          )}
          {general.length > 0 && (
            <details className="mt-4 rounded-md border border-line bg-surface">
              <summary className="cursor-pointer px-4 py-2.5 font-medium">Ghi chú chung ({general.length}) – không gắn với một nhân viên cụ thể</summary>
              <ul className="divide-y divide-line border-t border-line">
                {general.map((n, i) => (
                  <li key={i} className="px-4 py-2 text-sm">
                    <span className="font-medium">{NOTE_TITLE[n.rule] ?? n.rule}: </span>
                    {n.request_id && <Link className="text-primary hover:underline" to={`/lifecycle/requests?open=${n.request_id}`}>Request #{n.request_id} </Link>}
                    {n.asset_tag && <Mono>{n.asset_tag} </Mono>}
                    {n.message}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </>
      )}
    </>
  );
}
