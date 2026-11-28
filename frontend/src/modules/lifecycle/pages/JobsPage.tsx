import { useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Workflow } from "lucide-react";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { formatDate, formatRelative, isoDateVn } from "@/core/lib/format";
import { useDebounced } from "@/core/lib/useDebounced";
import { JOB_STATUS, JOB_WORKFLOWS, WORKFLOW_LABEL, jobStatusLabel, type JobListItem, type JobStatus } from "../jobs";

const CHIPS: { value: string; label: string }[] = [
  { value: "open", label: "Đang mở" },
  // Waiting for a person or stalled (worker not advancing it): the dashboard's "Cần xử lý tiếp".
  { value: "needs", label: "Cần xử lý tiếp" },
  { value: "late", label: "Trễ hạn" },
  { value: "draft", label: "Nháp" },
  { value: "waiting", label: "Chờ xử lý" },
  { value: "failed", label: "Lỗi" },
  { value: "scheduled", label: "Đã lên lịch" },
  { value: "completed", label: "Hoàn tất" },
  { value: "", label: "Tất cả" },
];

/** Views over the open jobs, filtered here (links from "Việc của tôi" land on exactly the counted jobs). */
const VIEWS: Record<string, ((j: JobListItem) => boolean) | undefined> = {
  // Waiting for a person or stalled (worker not advancing it): the dashboard's "Cần xử lý tiếp".
  needs: (j) => j.status === "waiting" || !!j.stalled_minutes,
  // Past the effective date and waiting on a person (a job stuck on an automatic step is not late work).
  late: (j) => !!j.params.effective_date && j.params.effective_date < isoDateVn() && !j.stalled_minutes && j.status !== "running" && j.status !== "scheduled",
  soon: (j) => !!j.params.effective_date && j.params.effective_date >= isoDateVn() && j.params.effective_date <= isoDateVn(3),
};

export function JobsPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const status = params.get("status") ?? "open";
  const workflow = params.get("workflow") ?? "";
  const assignee = params.get("assignee") === "me" ? "me" : ""; // from "Việc của tôi": only my Jobs
  const [q, setQ] = useState("");
  const debounced = useDebounced(q);
  const set = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    next.set(key, value);
    setParams(next, { replace: true });
  };
  const list = useQuery({
    queryKey: ["jobs", status, workflow, assignee, debounced],
    queryFn: () => api<{ total: number; counts: Record<string, number>; items: JobListItem[] }>(
      `/api/core/jobs?${new URLSearchParams({ status: VIEWS[status] ? "open" : status, workflow, assignee, workflows: JOB_WORKFLOWS, lean: "1", q: debounced, limit: "500" })}`),
    refetchInterval: 10000,
  });
  const items = (list.data?.items ?? []).filter((j) => !VIEWS[status] || VIEWS[status]!(j));
  const counts = list.data?.counts ?? {};
  const countOf = (v: string) =>
    v === "open" ? Object.entries(counts).filter(([k]) => !["completed", "cancelled"].includes(k)).reduce((n, [, c]) => n + c, 0)
      : v === "" ? Object.values(counts).reduce((n, c) => n + c, 0)
      : VIEWS[v] ? (status === v ? items.length : null) : counts[v] ?? 0;

  const columns = useMemo<ColumnDef<JobListItem, unknown>[]>(
    () => [
      {
        accessorKey: "subject_label",
        header: "Nhân viên",
        cell: (c) => (
          <span className="block">
            <button type="button" onClick={() => navigate(`/lifecycle/jobs/${c.row.original.id}`)} className="cursor-pointer text-left font-medium whitespace-nowrap hover:text-primary hover:underline">
              {c.row.original.subject_label}
            </button>
            <span className="block text-xs text-muted">{c.row.original.assignee ? `Phụ trách: ${c.row.original.assignee.name}` : "Chưa giao"}</span>
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
      {/* Same filter control as the Infra Support pages; empty states are dimmed, not hidden. */}
      {assignee && (
        <p className="mb-2 flex flex-wrap items-center gap-2 text-sm">
          <span>Chỉ Job bạn phụ trách.</span>
          {/* Without lifecycle.job.view_all the list holds only my jobs and the unassigned ones. */}
          <Button size="sm" variant="ghost" onClick={() => set("assignee", "")}>{can("lifecycle.job.view_all") ? "Xem Job của mọi người" : "Xem cả Job chưa ai nhận"}</Button>
        </p>
      )}
      <div className="mb-3 flex flex-wrap gap-1.5" role="group" aria-label="Lọc theo trạng thái">
        {CHIPS.map((c) => {
          const n = countOf(c.value);
          const on = status === c.value;
          // A status with no job is noise: hidden unless selected ("Đang mở" / "Tất cả" always stay).
          if (n === 0 && !on && c.value !== "open" && c.value !== "") return null;
          return (
            <Button
              key={c.value || "all"}
              size="sm"
              variant={on ? "primary" : "secondary"}
              aria-pressed={on}
              onClick={() => set("status", c.value)}
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
            <option value="lifecycle.transfer">Thuyên chuyển</option>
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
          card={(j) => (
            <>
              <span className="flex items-start justify-between gap-2">
                <span className="min-w-0">
                  <span className="block font-medium">{WORKFLOW_LABEL[j.workflow_key] ?? j.workflow_title} {j.subject_label}</span>
                  <span className="block text-sm text-muted">Hiệu lực {formatDate(j.params.effective_date)} · {j.assignee ? j.assignee.name : "Chưa giao"}</span>
                </span>
                <StatusBadge status={j.stalled_minutes ? "warning" : JOB_STATUS[j.status as JobStatus].status} label={jobStatusLabel(j)} />
              </span>
              <span className="mt-1 block text-sm text-muted tabular">Xong {j.progress.done}/{j.progress.total} bước · cập nhật {formatRelative(j.updated_at).toLowerCase()}</span>
            </>
          )}
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
