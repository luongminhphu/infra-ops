import { useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useAuth } from "@/core/auth/AuthProvider";
import type { ColumnDef } from "@tanstack/react-table";
import { Ticket, X } from "lucide-react";
import { api } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { useDebounced } from "@/core/lib/useDebounced";
import type { RtrRequest } from "../api";
import { KIND_LABEL } from "../labels";
import { JOB_KIND_LABEL, JOB_STATUS, type Job } from "../jobs";

function JobLink({ r }: { r: RtrRequest }) {
  const { can } = useAuth();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const jobs = useQuery({
    queryKey: ["jobs", "request", r.id],
    queryFn: () => api<{ items: Job[] }>(`/api/core/jobs?${new URLSearchParams({ q: r.id, status: "" })}`),
    enabled: can("lifecycle.job.view"),
  });
  const create = useMutation({
    mutationFn: () => api<Job>("/api/lifecycle/jobs", { method: "POST", json: { request_id: r.id } }),
    onSuccess: async (j) => {
      await qc.invalidateQueries({ queryKey: ["jobs"] });
      navigate(`/lifecycle/jobs/${j.id}`);
    },
  });
  if (!can("lifecycle.job.view") || !r.kind || !(r.kind in JOB_KIND_LABEL)) return null;
  const job = jobs.data?.items.find((j) => j.external_ref === r.id);
  return (
    <div className="rounded-sm border border-line p-3">
      <h3 className="mb-1.5 text-base font-semibold">Job {JOB_KIND_LABEL[r.kind]}</h3>
      {job ? (
        <p className="flex flex-wrap items-center gap-2 text-sm">
          <StatusBadge status={JOB_STATUS[job.status].status} label={JOB_STATUS[job.status].label} />
          <Link to={`/lifecycle/jobs/${job.id}`} className="font-medium text-primary hover:underline">Mở Job ({job.progress.done}/{job.progress.total} bước)</Link>
        </p>
      ) : can("lifecycle.job.execute") && !r.parse_errors.length ? (
        <Button variant="primary" size="sm" loading={create.isPending} onClick={() => create.mutate()}>Tạo Job từ request này</Button>
      ) : (
        <p className="text-sm text-muted">{r.parse_errors.length ? "Sửa lỗi đọc request trước khi tạo Job." : "Chưa có Job."}</p>
      )}
    </div>
  );
}

function RequestPanel({ id, onClose }: { id: string; onClose: () => void }) {
  const q = useQuery({ queryKey: ["lifecycle", "request", id], queryFn: () => api<RtrRequest>(`/api/lifecycle/requests/${id}`) });
  const r = q.data;
  return (
    <aside aria-label="Chi tiết request" className="sticky top-[calc(var(--topbar-h)+1.25rem)] max-h-[calc(100dvh-110px)] overflow-y-auto rounded-md border border-line bg-surface">
      <header className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">Request #{r?.display_id ?? id}</h2>
          <p className="text-sm text-muted">{r?.subject}</p>
        </div>
        <Button variant="ghost" size="icon" aria-label="Đóng chi tiết" onClick={onClose}>
          <X className="size-4" aria-hidden="true" />
        </Button>
      </header>
      {q.isLoading && <LoadingRows rows={6} />}
      {q.isError && <div className="p-4"><ErrorState error={q.error} /></div>}
      {r && (
        <div className="space-y-4 p-4">
          <JobLink r={r} />
          {r.parse_errors.length > 0 && (
            <div role="alert" className="rounded-sm bg-failed-soft p-2.5 text-sm">
              <p className="font-medium text-failed">Chưa đọc được đầy đủ thông tin</p>
              <ul className="mt-1 list-disc pl-5">{r.parse_errors.map((e) => <li key={e}>{e}</li>)}</ul>
            </div>
          )}
          <div>
            <h3 className="mb-1.5 text-base font-semibold">Thông tin đã đọc</h3>
            <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1 text-sm">
              {Object.entries(r.field_titles ?? {}).map(([k, title]) => (
                <div key={k} className="contents">
                  <dt className="text-muted">{title}</dt>
                  <dd className={k === "employee_id" ? "font-mono" : undefined}>{r.fields?.[k] ?? <span className="text-muted">—</span>}</dd>
                </div>
              ))}
              <dt className="text-muted">Phân loại</dt>
              <dd>{r.kind ? KIND_LABEL[r.kind] : <span className="text-waiting">Chưa phân loại</span>}</dd>
            </dl>
          </div>
          <details>
            <summary className="cursor-pointer text-sm font-medium">Nội dung gốc trên SDP</summary>
            <pre className="mt-2 max-h-80 overflow-auto rounded-sm bg-surface-2 p-2 text-xs whitespace-pre-wrap">{r.description_text}</pre>
          </details>
        </div>
      )}
    </aside>
  );
}

export function RequestsPage() {
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const debounced = useDebounced(q);
  const [kind, setKind] = useState("");
  const [errorsOnly, setErrorsOnly] = useState(false);
  const open = params.get("open");
  const list = useQuery({
    queryKey: ["lifecycle", "requests", debounced, kind, errorsOnly],
    queryFn: () =>
      api<{ total: number; items: RtrRequest[]; kinds: Record<string, number>; with_errors: number }>(
        `/api/lifecycle/requests?${new URLSearchParams({ q: debounced, kind, errors_only: String(errorsOnly), limit: "1000" })}`,
      ),
  });
  const setOpen = (id: string | null) => {
    const next = new URLSearchParams(params);
    if (id) next.set("open", id);
    else next.delete("open");
    setParams(next, { replace: true });
  };
  const columns = useMemo<ColumnDef<RtrRequest, unknown>[]>(
    () => [
      {
        accessorKey: "display_id",
        header: "Request",
        cell: (c) => (
          <button type="button" className="cursor-pointer font-medium hover:text-primary hover:underline" onClick={() => setOpen(c.row.original.id)}>
            #{c.row.original.display_id ?? c.row.original.id}
          </button>
        ),
      },
      { accessorKey: "created_at", header: "Ngày tạo", cell: (c) => <span className="tabular whitespace-nowrap">{formatDateTime(c.row.original.created_at)}</span> },
      { accessorKey: "employee_id", header: "Mã NV", cell: (c) => <Mono>{c.row.original.employee_id ?? "—"}</Mono> },
      { accessorKey: "full_name", header: "Họ tên", cell: (c) => <span className="whitespace-nowrap">{c.row.original.full_name ?? "—"}</span> },
      {
        accessorKey: "kind",
        header: "Loại biến động",
        cell: (c) => (
          <span className="whitespace-nowrap">
            {c.row.original.change_type_raw ?? "—"}
            {c.row.original.change_type_raw && (
              <span className="block text-xs text-muted">{c.row.original.kind ? `→ ${KIND_LABEL[c.row.original.kind]}` : "→ chưa phân loại"}</span>
            )}
          </span>
        ),
      },
      { accessorKey: "effective_date", header: "Hiệu lực", cell: (c) => <span className="tabular">{formatDate(c.row.original.effective_date)}</span> },
      { accessorKey: "department", header: "Phòng", cell: (c) => <span className="block max-w-44 truncate">{c.row.original.department ?? "—"}</span> },
      {
        id: "parse",
        header: "Đọc nội dung",
        cell: (c) =>
          c.row.original.parse_errors.length ? (
            // The first reason in the row (the rest in the detail panel), so nobody has to open each one.
            <span className="block max-w-72">
              <StatusBadge status="failed" label={`${c.row.original.parse_errors.length} lỗi`} />
              <span className="mt-0.5 block truncate text-xs text-muted" title={c.row.original.parse_errors.join("\n")}>{c.row.original.parse_errors[0]}</span>
            </span>
          ) : (
            <StatusBadge status="done" label="Đầy đủ" />
          ),
      },
    ],
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [params],
  );
  return (
    <>
      <PageHeader
        title="Request RTR"
        description="Request onboard/offboard do hệ thống RTR tạo trên ServiceDesk Plus, kèm thông tin infra-ops đọc được từ nội dung mô tả."
      />
      <form role="search" aria-label="Lọc request" className="mb-3 grid gap-3 sm:grid-cols-[2fr_1fr_auto]" onSubmit={(e) => e.preventDefault()}>
        <Field label="Tìm kiếm">
          <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Số request, Mã NV, họ tên" />
        </Field>
        <Field label="Phân loại">
          <Select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Tất cả</option>
            {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v} ({list.data?.kinds[k] ?? 0})</option>)}
            <option value="unknown">Chưa phân loại ({list.data?.kinds.unknown ?? 0})</option>
          </Select>
        </Field>
        <label className="flex cursor-pointer items-end gap-2 pb-1.5 text-sm whitespace-nowrap">
          <input type="checkbox" className="mb-0.5 size-4 accent-[var(--primary)]" checked={errorsOnly} onChange={(e) => setErrorsOnly(e.target.checked)} />
          Chỉ request lỗi ({list.data?.with_errors ?? 0})
        </label>
      </form>
      {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
      <div className={open ? "grid gap-4 xl:grid-cols-[minmax(0,1fr)_440px]" : undefined}>
        {list.isLoading ? (
          <div className="rounded-md border border-line bg-surface"><LoadingRows rows={8} /></div>
        ) : (
          <DataTable
            data={list.data?.items ?? []}
            columns={columns}
            caption="Request RTR"
            getRowId={(r) => r.id}
            onRowClick={(r) => setOpen(r.id)}
            selectedId={open}
            empty={<EmptyState icon={Ticket} title="Không có request phù hợp">Request RTR xuất hiện ở đây sau lần đồng bộ đầu tiên.</EmptyState>}
          />
        )}
        {open && <RequestPanel id={open} onClose={() => setOpen(null)} />}
      </div>
    </>
  );
}
