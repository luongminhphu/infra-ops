import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Download, TriangleAlert, UserSearch } from "lucide-react";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { buttonVariants } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge, type Status } from "@/core/ui/status";
import { fold, formatDateTime } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";

interface Candidate {
  employee_id: string;
  biostar_user_id: string | null;
  biostar_name: string;
  source: string | null;
  level: "exact" | "near";
  notes: string[];
}
type SdpStatus = "matched" | "near" | "ambiguous" | "claimed" | "none" | "unknown";
interface Item {
  sdp_user_id: string | null;
  name: string;
  email: string | null;
  department: string | null;
  current_value: string;
  status: SdpStatus;
  suggestion: Candidate | null;
  candidates: Candidate[];
}
export interface SdpMissingIds {
  run: { id: string; finished_at: string | null };
  sources: { sdp: "ok" | "failed"; biostar: "ok" | "failed"; failed_systems: string[] };
  items: Item[];
  counts: Partial<Record<SdpStatus, number>>;
  total_missing: number;
}

const STATUS: Record<SdpStatus, { label: string; status: Status }> = {
  matched: { label: "Khớp tên", status: "match" },
  near: { label: "Gần đúng", status: "warning" },
  ambiguous: { label: "Nhiều ứng viên", status: "warning" },
  claimed: { label: "Mã đã thuộc user khác", status: "mismatch" },
  none: { label: "Không tìm thấy", status: "skipped" },
  unknown: { label: "Không rõ (BioStar đọc lỗi)", status: "failed" },
};
const LEVEL: Record<string, string> = { exact: "Khớp tên", near: "Gần đúng" };

export function SdpMissingIdsPage() {
  const { can } = useAuth();
  const [onlyCandidates, setOnlyCandidates] = useState(true);
  const [status, setStatus] = useState<SdpStatus | "">("");
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["lifecycle", "sdp-missing-ids", onlyCandidates],
    queryFn: () => api<SdpMissingIds>(`/api/lifecycle/sdp-missing-ids?only_candidates=${onlyCandidates}`),
  });
  const d = list.data;
  const rows = useMemo(() => {
    const needle = fold(q.trim());
    return (d?.items ?? []).filter(
      (r) => (!status || r.status === status) && (!needle || fold(`${r.name} ${r.email ?? ""}`).includes(needle)),
    );
  }, [d, status, q]);

  const columns = useMemo<ColumnDef<Item, unknown>[]>(
    () => [
      {
        id: "name",
        header: "Tên SDP",
        cell: (c) => (
          <span className="block">
            <span className="font-medium">{c.row.original.name}</span>
            {c.row.original.email && <span className="block text-xs text-muted">{c.row.original.email}</span>}
          </span>
        ),
      },
      { id: "dept", header: "Phòng ban", cell: (c) => c.row.original.department ?? "—" },
      { id: "cur", header: "Giá trị hiện tại", cell: (c) => <Mono>{c.row.original.current_value || "—"}</Mono> },
      {
        id: "status",
        header: "Trạng thái",
        cell: (c) => <StatusBadge status={STATUS[c.row.original.status].status} label={STATUS[c.row.original.status].label} />,
      },
      { id: "id", header: "Mã NV đề xuất", cell: (c) => <Mono>{c.row.original.suggestion?.employee_id ?? "—"}</Mono> },
      {
        id: "bs",
        header: "Tên BioStar",
        cell: (c) => {
          const s = c.row.original.suggestion;
          return s ? (
            <span className="block">
              {s.biostar_name}
              {s.source && <span className="block text-xs text-muted">{s.source}</span>}
            </span>
          ) : (
            "—"
          );
        },
      },
      {
        id: "note",
        header: "Mức khớp / ghi chú",
        cell: (c) => {
          const s = c.row.original.suggestion;
          return s ? (
            <span className="block max-w-72">
              {LEVEL[s.level]}
              {s.notes.length > 0 && <span className="block text-xs text-muted">{s.notes.join("; ")}</span>}
            </span>
          ) : (
            "—"
          );
        },
      },
      {
        id: "others",
        header: "Ứng viên khác",
        cell: (c) => {
          const others = c.row.original.candidates.filter((x) => x !== c.row.original.suggestion);
          if (!others.length) return "—";
          return (
            <details>
              <summary className="cursor-pointer text-primary">{others.length} ứng viên</summary>
              <ul className="mt-1 space-y-0.5 text-xs">
                {others.map((o, i) => (
                  <li key={`${o.employee_id}-${i}`}>
                    <Mono>{o.employee_id}</Mono> – {o.biostar_name}
                    {o.source ? ` (${o.source})` : ""}
                  </li>
                ))}
              </ul>
            </details>
          );
        },
      },
    ],
    [],
  );

  const chip = (value: SdpStatus | "", label: string, n: number) => (
    <button
      key={value || "all"}
      type="button"
      aria-pressed={status === value}
      onClick={() => setStatus(value)}
      className={cn(
        "cursor-pointer rounded-sm border px-2.5 py-1 text-sm",
        status === value ? "border-primary bg-primary-soft font-medium text-primary" : "border-line-strong bg-surface hover:bg-surface-2",
      )}
    >
      {label} ({n})
    </button>
  );
  const bsFailed = d?.sources.failed_systems.filter((s) => s.startsWith("biostar")) ?? [];

  return (
    <>
      <PageHeader
        title="User SDP thiếu Mã NV"
        description={
          <>
            User ServiceDesk Plus chưa có Mã NV, kèm Mã NV gợi ý từ user BioStar 2 cùng tên.
            <HelpTip title="Cách đọc trang này">
              Đối chiếu tên user SDP chưa có Mã NV với user BioStar 2 (5 số cuối Mã NV). Đây là gợi ý – kiểm tra rồi sửa Mã NV trên SDP/AD; app không ghi gì vào SDP.
            </HelpTip>
          </>
        }
        actions={
          <a
            className={buttonVariants({ variant: "secondary", size: "md" })}
            href={`/api/lifecycle/sdp-missing-ids.xlsx?only_candidates=${onlyCandidates}`}
            download
          >
            <Download className="size-4" aria-hidden="true" />
            Xuất Excel
          </a>
        }
      />
      {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
      {d && (
        <div className="mb-3 space-y-2 text-sm">
          {d.sources.sdp === "failed" && (
            <p role="alert" className="flex items-start gap-2 rounded-sm bg-failed-soft p-2.5 text-failed">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              Lần đồng bộ gần nhất không đọc được user SDP – chưa có danh sách.
            </p>
          )}
          {bsFailed.length > 0 && (
            <p role="alert" className="flex items-start gap-2 rounded-sm bg-waiting-soft p-2.5 text-waiting">
              <TriangleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
              BioStar ({bsFailed.join(", ")}) đọc lỗi – chưa gợi ý được.
            </p>
          )}
          <p className="text-muted">
            Theo dữ liệu đồng bộ lúc {formatDateTime(d.run.finished_at)}
            {can("lifecycle.reconciliation.sync") && (
              <>
                {" · "}
                <Link to="/lifecycle/sync" className="font-medium text-primary hover:underline">Đồng bộ</Link>
              </>
            )}
          </p>
        </div>
      )}
      <form role="search" aria-label="Lọc user SDP" className="mb-3 space-y-3" onSubmit={(e) => e.preventDefault()}>
        <div className="grid gap-3 sm:grid-cols-[2fr_auto] sm:items-end">
          <Field label="Tìm kiếm">
            <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tên hoặc email" />
          </Field>
          <label className="flex cursor-pointer items-center gap-2 pb-1.5 text-sm whitespace-nowrap">
            <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={onlyCandidates} onChange={(e) => setOnlyCandidates(e.target.checked)} />
            Chỉ user có ứng viên
          </label>
        </div>
        {d && (
          <div className="flex flex-wrap gap-2" role="group" aria-label="Lọc theo trạng thái">
            {chip("", "Tất cả", d.items.length)}
            {(Object.keys(STATUS) as SdpStatus[])
              .filter((s) => d.counts[s])
              .map((s) => chip(s, STATUS[s].label, d.counts[s] ?? 0))}
          </div>
        )}
      </form>
      {list.isLoading ? (
        <div className="rounded-md border border-line bg-surface"><LoadingRows rows={8} /></div>
      ) : (
        d && (
          <DataTable
            data={rows}
            columns={columns}
            caption="User SDP thiếu Mã NV"
            getRowId={(r) => r.sdp_user_id ?? r.name}
            empty={
              <EmptyState icon={UserSearch} title="Không có user phù hợp">
                {d.sources.sdp === "failed" ? "Chưa đọc được user SDP." : "Không có user SDP nào thiếu Mã NV với bộ lọc này."}
              </EmptyState>
            }
          />
        )
      )}
    </>
  );
}
