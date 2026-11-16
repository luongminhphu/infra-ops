import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { ChevronDown, Download, GitCompareArrows, RefreshCw, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { DataTable, type Density } from "@/core/ui/data-table";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { Menu, MenuItem } from "@/core/ui/menu";
import { StatusBadge } from "@/core/ui/status";
import { formatDate, formatRelative } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import { useRules, useSyncNow, useSyncStatus, type ReconResponse, type ReconRow } from "../api";
import { CellChip } from "../CellChip";
import { KIND_LABEL, NOTE_TITLE, SEVERITY, SOURCE_LABEL } from "../labels";
import { EmployeePanel } from "./EmployeePanel";

const FILTER_KEYS = ["q", "severity", "rule", "kind", "department", "only_issues"] as const;

function SyncBanner({ data }: { data: ReconResponse }) {
  const run = data.run!;
  const failed = Object.keys(run.errors ?? {});
  return (
    <div className="mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-sm text-muted">
      <span>
        Dữ liệu lúc <strong className="font-medium text-ink">{formatRelative(run.finished_at)}</strong> ·{" "}
        {run.stats.employees ?? 0} nhân viên · {run.stats.requests ?? 0} request RTR · {run.stats.assets ?? 0} tài sản ·{" "}
        {run.stats.biostar_users ?? 0} user BioStar
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

export function ReconciliationPage() {
  const { can } = useAuth();
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string | null>(params.get("emp"));
  const [density, setDensity] = useState<Density>("compact");
  const [search, setSearch] = useState(params.get("q") ?? "");
  const rules = useRules();
  const syncStatus = useSyncStatus();
  const syncNow = useSyncNow();
  const filters = Object.fromEntries(FILTER_KEYS.map((k) => [k, params.get(k) ?? ""])) as Record<(typeof FILTER_KEYS)[number], string>;

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  useEffect(() => {
    const t = window.setTimeout(() => search !== filters.q && setFilter("q", search), 300);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [search]);

  const qs = new URLSearchParams(Object.entries(filters).filter(([, v]) => v));
  const q = useQuery({
    queryKey: ["lifecycle", "recon", filters],
    queryFn: () => api<ReconResponse>(`/api/lifecycle/reconciliation?${qs}&limit=1000`),
  });

  // Refresh when a sync finishes.
  const lastFinished = syncStatus.data?.task?.last_finished_at;
  useEffect(() => {
    if (lastFinished) void q.refetch();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [lastFinished]);

  const ruleTitle = useMemo(() => Object.fromEntries((rules.data ?? []).map((r) => [r.code, r.title])), [rules.data]);

  const columns = useMemo<ColumnDef<ReconRow, unknown>[]>(() => {
    const flagged = (row: ReconRow, system: string) => row.issues.some((i) => !i.acked && i.system === system);
    return [
      {
        accessorKey: "employee_id",
        header: "Mã NV",
        cell: (c) => (
          <button type="button" className="cursor-pointer font-mono text-[0.8125rem] hover:text-primary hover:underline" onClick={() => setSelected(c.row.original.employee_id)}>
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
          return (
            <span title={r.issues.filter((i) => !i.acked).map((i) => ruleTitle[i.rule] ?? i.rule).join("\n")}>
              <StatusBadge status={SEVERITY[r.max_severity!].status} label={`${r.issue_count} · ${SEVERITY[r.max_severity!].short}`} />
            </span>
          );
        },
      },
    ];
  }, [ruleTitle]);

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

  const data = q.data;
  const sev = data?.facets.severity ?? {};
  const syncing = syncNow.isPending || syncStatus.data?.task?.running;

  return (
    <>
      <PageHeader
        title="Đối soát theo Mã NV"
        description="Mỗi dòng là một nhân viên, so sánh request RTR, tài sản trên ServiceDesk Plus và user BioStar. Ô có biểu tượng cảnh báo và viền hồng là hệ thống đang lệch."
        actions={
          <>
            {can("lifecycle.reconciliation.sync") && (
              <Button onClick={() => syncNow.mutate()} loading={!!syncing}>
                {!syncing && <RefreshCw className="size-4" aria-hidden="true" />}
                {syncing ? "Đang đồng bộ…" : "Đồng bộ ngay"}
              </Button>
            )}
            <Menu
              label="Xuất dữ liệu"
              trigger={(p) => (
                <Button {...p} disabled={!data?.run}>
                  <Download className="size-4" aria-hidden="true" />
                  Xuất danh sách
                  <ChevronDown className="size-3.5" aria-hidden="true" />
                </Button>
              )}
            >
              <MenuItem onSelect={() => download("xlsx")}>Excel (.xlsx) – theo bộ lọc hiện tại</MenuItem>
              <MenuItem onSelect={() => download("csv")}>CSV (.csv) – theo bộ lọc hiện tại</MenuItem>
            </Menu>
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
            action={can("lifecycle.reconciliation.sync") && <Button variant="primary" onClick={() => syncNow.mutate()} loading={!!syncing}>Đồng bộ lần đầu</Button>}
          >
            Cần kết nối ServiceDesk Plus và BioStar có credential Read, sau đó chạy đồng bộ. Xem trạng thái ở trang{" "}
            <Link className="text-primary underline" to="/lifecycle/sync">Đồng bộ dữ liệu</Link>.
          </EmptyState>
        </div>
      )}
      {data?.run && (
        <>
          <SyncBanner data={data} />
          <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Lọc theo mức độ">
            {([["", `Tất cả (${data.facets.total_rows ?? 0})`], ["high", `${SEVERITY.high.label} (${sev.high ?? 0})`], ["medium", `${SEVERITY.medium.label} (${sev.medium ?? 0})`], ["low", `${SEVERITY.low.label} (${sev.low ?? 0})`]] as const).map(([value, label]) => (
              <button
                key={value || "all"}
                type="button"
                aria-pressed={filters.severity === value}
                onClick={() => setFilter("severity", value)}
                className={cn(
                  "h-7 cursor-pointer rounded-full border px-3 text-sm transition-colors duration-150",
                  filters.severity === value ? "border-primary bg-primary-soft font-medium text-primary" : "border-line-strong bg-surface hover:bg-surface-2",
                )}
              >
                {label}
              </button>
            ))}
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
                {(rules.data ?? []).map((r) => <option key={r.code} value={r.code}>{r.title} ({data.facets.rules?.[r.code] ?? 0})</option>)}
              </Select>
            </Field>
            <Field label="Phòng ban">
              <Select value={filters.department} onChange={(e) => setFilter("department", e.target.value)}>
                <option value="">Tất cả</option>
                {(data.facets.departments ?? []).map((d) => <option key={d} value={d}>{d}</option>)}
              </Select>
            </Field>
            <div className="flex items-end gap-3 pb-1.5">
              <label className="flex cursor-pointer items-center gap-2 text-sm whitespace-nowrap">
                <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={filters.only_issues === "true"} onChange={(e) => setFilter("only_issues", e.target.checked ? "true" : "")} />
                Chỉ dòng lệch
              </label>
              <label className="flex cursor-pointer items-center gap-2 text-sm whitespace-nowrap">
                <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={density === "comfortable"} onChange={(e) => setDensity(e.target.checked ? "comfortable" : "compact")} />
                Dòng thoáng
              </label>
            </div>
          </form>
          <p className="mb-2 text-sm text-muted" role="status">{data.total} nhân viên phù hợp</p>
          <div className={selected ? "grid gap-4 xl:grid-cols-[minmax(0,1fr)_440px]" : undefined}>
            <DataTable
              data={data.items}
              columns={columns}
              caption="Đối soát theo Mã nhân viên"
              getRowId={(r) => r.employee_id}
              onRowClick={(r) => setSelected(r.employee_id)}
              selectedId={selected}
              density={density}
              maxHeight="calc(100dvh - 330px)"
              empty={<EmptyState icon={GitCompareArrows} title="Không có nhân viên phù hợp">Bỏ bớt bộ lọc để xem thêm.</EmptyState>}
            />
            {selected && <EmployeePanel employeeId={selected} onClose={() => setSelected(null)} />}
          </div>
          {data.notes.length > 0 && (
            <details className="mt-4 rounded-md border border-line bg-surface">
              <summary className="cursor-pointer px-4 py-2.5 font-medium">Ghi chú chung ({data.notes.length}) – không gắn với một nhân viên cụ thể</summary>
              <ul className="divide-y divide-line border-t border-line">
                {data.notes.map((n, i) => (
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
