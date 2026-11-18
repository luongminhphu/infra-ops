import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Workflow } from "lucide-react";
import { api } from "@/core/api/client";
import { DataTable } from "@/core/ui/data-table";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { formatDate, formatRelative } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import { JOB_STATUS, WORKFLOW_LABEL, type Job, type JobStatus } from "../jobs";

const CHIPS: { value: string; label: string }[] = [
  { value: "open", label: "Đang mở" },
  { value: "draft", label: "Nháp" },
  { value: "waiting", label: "Chờ xử lý" },
  { value: "failed", label: "Lỗi" },
  { value: "scheduled", label: "Đã lên lịch" },
  { value: "completed", label: "Hoàn tất" },
  { value: "", label: "Tất cả" },
];

export function JobsPage() {
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "open";
  const workflow = params.get("workflow") ?? "";
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q), 300);
    return () => window.clearTimeout(t);
  }, [q]);
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };
  const list = useQuery({
    queryKey: ["jobs", status, workflow, debounced],
    queryFn: () => api<{ total: number; counts: Record<string, number>; items: Job[] }>(
      `/api/core/jobs?${new URLSearchParams({ status, workflow, q: debounced, limit: "500" })}`),
    refetchInterval: 10000,
  });
  const counts = list.data?.counts ?? {};
  const countOf = (v: string) =>
    v === "open" ? Object.entries(counts).filter(([k]) => !["completed", "cancelled"].includes(k)).reduce((n, [, c]) => n + c, 0)
      : v === "" ? Object.values(counts).reduce((n, c) => n + c, 0) : counts[v] ?? 0;

  const columns = useMemo<ColumnDef<Job, unknown>[]>(
    () => [
      {
        accessorKey: "subject_label",
        header: "Nhân viên",
        cell: (c) => (
          <button type="button" onClick={() => navigate(`/lifecycle/jobs/${c.row.original.id}`)} className="cursor-pointer text-left font-medium whitespace-nowrap hover:text-primary hover:underline">
            {c.row.original.subject_label}
          </button>
        ),
      },
      { accessorKey: "workflow_key", header: "Quy trình", cell: (c) => WORKFLOW_LABEL[c.row.original.workflow_key] ?? c.row.original.workflow_title },
      { accessorKey: "external_ref", header: "Request", cell: (c) => <Mono>#{c.row.original.params.request_display_id ?? c.row.original.external_ref}</Mono> },
      { id: "effective", header: "Ngày hiệu lực", accessorFn: (j) => j.params.effective_date, cell: (c) => <span className="tabular">{formatDate(c.row.original.params.effective_date)}</span> },
      {
        id: "current",
        header: "Bước hiện tại",
        enableSorting: false,
        cell: (c) => {
          const j = c.row.original;
          const st = j.steps.find((s) => s.key === j.current_step);
          return <span className="block max-w-64 truncate text-muted">{st ? st.title : j.status === "draft" ? "Chưa bắt đầu" : "—"}</span>;
        },
      },
      {
        id: "progress",
        header: "Tiến độ",
        accessorFn: (j) => j.progress.done / Math.max(1, j.progress.total),
        cell: (c) => {
          const p = c.row.original.progress;
          return (
            <span className="flex items-center gap-2">
              <span className="block h-1.5 w-16 rounded-full bg-surface-2" aria-hidden="true">
                <span className="block h-1.5 rounded-full bg-primary" style={{ width: `${(p.done / Math.max(1, p.total)) * 100}%` }} />
              </span>
              <span className="tabular text-sm">{p.done}/{p.total}</span>
            </span>
          );
        },
      },
      { accessorKey: "status", header: "Trạng thái", cell: (c) => <StatusBadge status={JOB_STATUS[c.row.original.status as JobStatus].status} label={JOB_STATUS[c.row.original.status as JobStatus].label} /> },
      { accessorKey: "updated_at", header: "Cập nhật", cell: (c) => <span className="text-sm whitespace-nowrap text-muted">{formatRelative(c.row.original.updated_at)}</span> },
    ],
    [navigate],
  );

  return (
    <>
      <PageHeader
        title="Job onboard / offboard"
        description="Mỗi request RTR (nhân viên mới / nghỉ việc) có một Job. Job nháp được tạo tự động sau mỗi lần đồng bộ; Helpdesk kiểm tra, chọn tài sản rồi bắt đầu."
      />
      <div className="mb-3 flex flex-wrap gap-2" role="group" aria-label="Lọc theo trạng thái">
        {CHIPS.map((c) => (
          <button
            key={c.value || "all"}
            type="button"
            aria-pressed={status === c.value}
            onClick={() => set("status", c.value)}
            className={cn(
              "h-7 cursor-pointer rounded-full border px-3 text-sm transition-colors duration-150",
              status === c.value ? "border-primary bg-primary-soft font-medium text-primary" : "border-line-strong bg-surface hover:bg-surface-2",
            )}
          >
            {c.label} ({countOf(c.value)})
          </button>
        ))}
      </div>
      <form role="search" aria-label="Lọc Job" className="mb-3 grid gap-3 sm:grid-cols-[2fr_1fr]" onSubmit={(e) => e.preventDefault()}>
        <Field label="Tìm kiếm">
          <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Mã NV, họ tên, số request" />
        </Field>
        <Field label="Quy trình">
          <Select value={workflow} onChange={(e) => set("workflow", e.target.value)}>
            <option value="">Tất cả</option>
            <option value="lifecycle.onboard">Onboard</option>
            <option value="lifecycle.offboard">Offboard</option>
          </Select>
        </Field>
      </form>
      {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
      {list.isLoading ? (
        <div className="rounded-md border border-line bg-surface"><LoadingRows rows={6} /></div>
      ) : (
        <DataTable
          data={list.data?.items ?? []}
          columns={columns}
          caption="Job onboard / offboard"
          getRowId={(j) => j.id}
          onRowClick={(j) => navigate(`/lifecycle/jobs/${j.id}`)}
          empty={
            <EmptyState icon={Workflow} title="Không có Job phù hợp">
              Job được tạo từ request RTR loại Nhân viên mới hoặc Nghỉ việc sau khi đồng bộ, hoặc từ trang Request RTR.
            </EmptyState>
          }
        />
      )}
    </>
  );
}
