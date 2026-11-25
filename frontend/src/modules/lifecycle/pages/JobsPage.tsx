import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Workflow } from "lucide-react";
import { api } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { formatDate, formatRelative } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import { JOB_STATUS, JOB_WORKFLOWS, WORKFLOW_LABEL, jobStatusLabel, type Job, type JobStatus } from "../jobs";

const CHIPS: { value: string; label: string }[] = [
  { value: "open", label: "Đang mở" },
  // Waiting for a person or stalled (worker not advancing it): the dashboard's "Cần xử lý tiếp".
  { value: "needs", label: "Cần xử lý tiếp" },
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
      `/api/core/jobs?${new URLSearchParams({ status: status === "needs" ? "open" : status, workflow, workflows: JOB_WORKFLOWS, q: debounced, limit: "500" })}`),
    refetchInterval: 10000,
  });
  const items = (list.data?.items ?? []).filter((j) => status !== "needs" || j.status === "waiting" || !!j.stalled_minutes);
  const counts = list.data?.counts ?? {};
  const countOf = (v: string) =>
    v === "open" ? Object.entries(counts).filter(([k]) => !["completed", "cancelled"].includes(k)).reduce((n, [, c]) => n + c, 0)
      : v === "" ? Object.values(counts).reduce((n, c) => n + c, 0)
      : v === "needs" ? (status === "needs" ? items.length : null) : counts[v] ?? 0;

  const columns = useMemo<ColumnDef<Job, unknown>[]>(
    () => [
      {
        accessorKey: "subject_label",
        header: "Nhân viên",
        cell: (c) => (
          <span className="block">
            <button type="button" onClick={() => navigate(`/lifecycle/jobs/${c.row.original.id}`)} className="cursor-pointer text-left font-medium whitespace-nowrap hover:text-primary hover:underline">
              {c.row.original.subject_label}
            </button>
            <span className="block text-xs text-muted">{c.row.original.assignee ? `Phụ trách: ${c.row.original.assignee.name}` : "Chưa có người phụ trách"}</span>
          </span>
        ),
      },
      { accessorKey: "workflow_key", header: "Quy trình", cell: (c) => WORKFLOW_LABEL[c.row.original.workflow_key] ?? c.row.original.workflow_title },
      { accessorKey: "external_ref", header: "Request", cell: (c) => <Mono>#{c.row.original.params.request_display_id ?? c.row.original.external_ref}</Mono> },
      { id: "effective", header: "Ngày hiệu lực", accessorFn: (j) => j.params.effective_date, cell: (c) => <span className="tabular">{formatDate(c.row.original.params.effective_date)}</span> },
      {
        id: "progress",
        header: "Tiến độ",
        accessorFn: (j) => j.progress.done / Math.max(1, j.progress.total),
        cell: (c) => {
          const j = c.row.original;
          const p = j.progress;
          const st = j.steps.find((s) => s.key === j.current_step);
          return (
            <span className="block">
              <span className="flex items-center gap-2">
                <span className="block h-1.5 w-16 rounded-full bg-surface-2" aria-hidden="true">
                  <span className="block h-1.5 rounded-full bg-primary" style={{ width: `${(p.done / Math.max(1, p.total)) * 100}%` }} />
                </span>
                <span className="tabular text-sm">{p.done}/{p.total}</span>
              </span>
              <span className="block max-w-56 truncate text-xs text-muted">{st ? st.title : j.status === "draft" ? "Chưa bắt đầu" : ""}</span>
            </span>
          );
        },
      },
      {
        accessorKey: "status",
        header: "Trạng thái",
        cell: (c) => (
          <StatusBadge
            status={c.row.original.stalled_minutes ? "warning" : JOB_STATUS[c.row.original.status as JobStatus].status}
            label={jobStatusLabel(c.row.original)}
          />
        ),
      },
      { accessorKey: "updated_at", header: "Cập nhật", cell: (c) => <span className="text-sm whitespace-nowrap text-muted">{formatRelative(c.row.original.updated_at)}</span> },
    ],
    [navigate],
  );

  return (
    <>
      <PageHeader
        title="Job onboard / offboard"
        description="Mỗi request RTR (nhân viên mới / nghỉ việc) có một Job. Job nháp được tạo tự động sau mỗi lần đồng bộ; Operator kiểm tra, chọn tài sản rồi bắt đầu."
      />
      {/* Same filter control as the Helpdesk pages; empty states are dimmed, not hidden. */}
      <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Lọc theo trạng thái">
        {CHIPS.map((c) => {
          const n = countOf(c.value);
          const on = status === c.value;
          return (
            <Button
              key={c.value || "all"}
              size="sm"
              variant={on ? "primary" : "secondary"}
              aria-pressed={on}
              onClick={() => set("status", c.value)}
              className={cn(!on && n === 0 && "text-muted")}
            >
              {c.label}{n !== null && <> <span className="tabular">{n}</span></>}
            </Button>
          );
        })}
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
          data={items}
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
