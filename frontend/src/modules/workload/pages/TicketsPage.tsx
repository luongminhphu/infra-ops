import { useEffect, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { ArrowRightLeft, ListChecks, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { useSearchParams } from "react-router-dom";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input, Select, Textarea } from "@/core/ui/field";
import { StatusBadge, type Status } from "@/core/ui/status";
import { formatDateTime, formatRelative } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import { useSyncNow, useSyncStatus, useTechnicians, type Ticket, type TicketState } from "../api";

const STATES: { key: TicketState; label: string }[] = [
  { key: "open", label: "Đang chờ thực hiện" },
  { key: "overdue", label: "Quá hạn" },
  { key: "done", label: "Đã thực hiện" },
  { key: "cancelled", label: "Đã huỷ" },
  { key: "all", label: "Tất cả" },
];

function stateBadge(t: Ticket): { status: Status; label: string } {
  if (t.state === "done") return t.overdue ? { status: "warning", label: "Xong, trễ hạn" } : { status: "done", label: "Xong đúng hạn" };
  if (t.state === "cancelled") return { status: "skipped", label: "Đã huỷ" };
  return t.overdue ? { status: "failed", label: "Quá hạn" } : { status: "waiting", label: "Đang chờ" };
}

function ReassignDialog({ ids, onClose, onDone }: { ids: string[]; onClose: () => void; onDone: () => void }) {
  const techs = useTechnicians();
  const qc = useQueryClient();
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const submit = useMutation({
    mutationFn: () =>
      api<{ id: string }>("/api/workload/tickets/reassign", { method: "POST", json: { ticket_ids: ids, to_technician_id: to, reason } }),
    onSuccess: () => {
      toast.success("Đã gửi yêu cầu chuyển việc. infra-ops đang cập nhật Technician trên SDP.");
      void qc.invalidateQueries({ queryKey: ["workload"] });
      onDone();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const options = (techs.data?.items ?? []).filter((t) => t.active);
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Chuyển ${ids.length} task`}
      description="Technician của các request được đổi trực tiếp trên ServiceDesk Plus và ghi nhật ký kiểm tra."
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button
            variant="primary"
            loading={submit.isPending}
            onClick={() => {
              setTouched(true);
              if (to) submit.mutate();
            }}
          >
            Chuyển việc
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <Field label="Chuyển cho" required error="Chọn người nhận việc." showError={touched && !to}>
          <Select value={to} onChange={(e) => setTo(e.target.value)} onBlur={() => setTouched(true)}>
            <option value="">— Chọn Operator —</option>
            {options.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} · đang có {t.open} task{t.user ? "" : " (chưa liên kết tài khoản)"}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Lý do" hint="Ví dụ: Nghỉ phép 24/11–28/11, chuyển cho người backup.">
          <Textarea rows={2} maxLength={500} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}

interface ReassignJob {
  id: string;
  status: string;
  subject_label: string;
  created_by: string;
  created_at: string;
  steps: { key: string; status: string; message: string }[];
}

/** Reassignments still running or failed, so a refused SDP write never goes unnoticed. */
function PendingReassignments() {
  const qc = useQueryClient();
  const jobs = useQuery({
    queryKey: ["workload", "reassign-jobs"],
    queryFn: () => api<{ items: ReassignJob[] }>("/api/core/jobs?workflow=workload.reassign&status=open&limit=20"),
    refetchInterval: 10_000,
  });
  const retry = useMutation({
    mutationFn: (id: string) => api(`/api/core/jobs/${id}/steps/reassign/retry`, { method: "POST" }),
    onSuccess: () => {
      toast.success("Đã gửi lại yêu cầu chuyển việc.");
      void qc.invalidateQueries({ queryKey: ["workload"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api(`/api/core/jobs/${id}/cancel`, { method: "POST", json: { reason: "Manager bỏ yêu cầu chuyển việc lỗi" } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["workload"] }),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const items = jobs.data?.items ?? [];
  if (!items.length) return null;
  return (
    <section aria-label="Yêu cầu chuyển việc đang xử lý" className="mb-3 space-y-1.5">
      {items.map((j) => {
        const failed = j.status === "failed";
        return (
          <div key={j.id} className={cn("flex flex-wrap items-center gap-3 rounded-md border px-3 py-2 text-sm", failed ? "border-failed bg-failed-soft" : "border-line bg-surface")}>
            <StatusBadge status={failed ? "failed" : "in_progress"} label={failed ? "Chuyển việc lỗi" : "Đang chuyển"} />
            <span className="font-medium">{j.subject_label}</span>
            <span className="text-muted">{j.created_by} · {formatDateTime(j.created_at)}</span>
            {failed && <span className="basis-full text-failed">{j.steps[0]?.message}</span>}
            {failed && (
              <>
                <Button size="sm" loading={retry.isPending} onClick={() => retry.mutate(j.id)}>Thử lại</Button>
                <Button size="sm" variant="ghost" loading={cancel.isPending} onClick={() => cancel.mutate(j.id)}>Bỏ yêu cầu</Button>
              </>
            )}
          </div>
        );
      })}
    </section>
  );
}

export function TicketsPage() {
  const { can } = useAuth();
  const canAssign = can("workload.tickets.assign");
  const [params, setParams] = useSearchParams();
  const state = (params.get("state") as TicketState) || "open";
  const tech = params.get("tech") ?? "";
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [dialog, setDialog] = useState(false);
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q), 300);
    return () => window.clearTimeout(t);
  }, [q]);
  const set = (k: string, v: string) => {
    const next = new URLSearchParams(params);
    if (v) next.set(k, v);
    else next.delete(k);
    setParams(next, { replace: true });
    setSelected([]);
  };
  const techs = useTechnicians();
  const sync = useSyncStatus();
  const syncNow = useSyncNow();
  const list = useQuery({
    queryKey: ["workload", "tickets", state, tech, debounced],
    queryFn: () =>
      api<{ total: number; items: Ticket[] }>(`/api/workload/tickets?${new URLSearchParams({ state, technician_id: tech, q: debounced })}`),
  });
  const items = list.data?.items ?? [];
  const selectable = items.filter((t) => t.state === "open").map((t) => t.id);
  const toggle = (id: string) => setSelected((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id]));
  const allOn = selectable.length > 0 && selectable.every((id) => selected.includes(id));

  const columns = useMemo<ColumnDef<Ticket, unknown>[]>(() => {
    const cols: ColumnDef<Ticket, unknown>[] = [
      { accessorKey: "display_id", header: "Request", cell: (c) => <Mono>#{c.row.original.display_id ?? c.row.original.id}</Mono> },
      { accessorKey: "subject", header: "Tiêu đề", cell: (c) => <span className="block max-w-96 truncate" title={c.row.original.subject}>{c.row.original.subject}</span> },
      { accessorKey: "requester_name", header: "Người yêu cầu", cell: (c) => c.row.original.requester_name ?? "—" },
      { accessorKey: "technician_name", header: "Người xử lý", cell: (c) => <span className="whitespace-nowrap">{c.row.original.technician_name ?? <span className="text-muted">Chưa giao</span>}</span> },
      { accessorKey: "status", header: "Trạng thái SDP" },
      { id: "sla", header: "Tiến độ", cell: (c) => <StatusBadge {...stateBadge(c.row.original)} /> },
      { accessorKey: "due_by", header: "Hạn xử lý", cell: (c) => <span className="tabular whitespace-nowrap">{formatDateTime(c.row.original.due_by)}</span> },
      {
        accessorKey: state === "done" || state === "cancelled" ? "completed_at" : "created_at",
        header: state === "done" ? "Hoàn tất" : "Ngày tạo",
        cell: (c) => <span className="tabular whitespace-nowrap">{formatDateTime(state === "done" ? c.row.original.completed_at : c.row.original.created_at)}</span>,
      },
    ];
    if (!canAssign) return cols;
    return [
      {
        id: "select",
        enableSorting: false,
        size: 40,
        header: () => (
          <input
            type="checkbox"
            className="size-4 accent-[var(--primary)]"
            aria-label="Chọn tất cả task đang mở"
            checked={allOn}
            disabled={!selectable.length}
            onChange={() => setSelected(allOn ? [] : selectable)}
          />
        ),
        cell: (c) =>
          c.row.original.state === "open" ? (
            <input
              type="checkbox"
              className="size-4 accent-[var(--primary)]"
              aria-label={`Chọn request ${c.row.original.display_id ?? c.row.original.id}`}
              checked={selected.includes(c.row.original.id)}
              onChange={() => toggle(c.row.original.id)}
            />
          ) : null,
      },
      ...cols,
    ];
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, canAssign, selected, allOn, selectable.join()]);

  const t = techs.data;
  return (
    <>
      <PageHeader
        title="Công việc Helpdesk"
        description={
          <>
            Task Helpdesk/Support trên ServiceDesk Plus theo từng Operator. Đồng bộ gần nhất:{" "}
            {formatRelative(sync.data?.last_synced_at).toLowerCase()}.
            {sync.data?.task?.last_error && <span className="block text-failed">Lần đồng bộ gần nhất lỗi: {sync.data.task.last_error}</span>}
          </>
        }
        actions={
          can("workload.sync.run") && (
            <Button onClick={() => syncNow.mutate()} loading={syncNow.isPending || sync.data?.task?.running}>
              <RefreshCw className="size-4" aria-hidden="true" />
              Đồng bộ ngay
            </Button>
          )
        }
      />

      {t && !t.view_all && !t.me && (
        <p role="status" className="mb-4 rounded-md border border-waiting bg-waiting-soft px-3 py-2 text-sm">
          Tài khoản của bạn chưa liên kết với technician trên ServiceDesk Plus nên chưa có task nào. Liên hệ Manager để liên kết.
        </p>
      )}
      {t?.view_all && (
        <nav aria-label="Lọc theo Operator" className="mb-4 grid gap-2 [grid-template-columns:repeat(auto-fill,minmax(210px,1fr))]">
          {[
            ...(t.me ? [{ key: "me", name: "Việc của tôi", open: t.items.find((x) => x.id === t.me)?.open ?? 0, overdue: t.items.find((x) => x.id === t.me)?.overdue ?? 0 }] : []),
            { key: "", name: "Tất cả Operator", open: t.items.reduce((a, x) => a + x.open, 0) + t.unassigned.open, overdue: t.items.reduce((a, x) => a + x.overdue, 0) + t.unassigned.overdue },
            ...t.items.filter((x) => x.open || x.user).map((x) => ({ key: x.id, name: x.name, open: x.open, overdue: x.overdue })),
            ...(t.unassigned.open ? [{ key: "none", name: "Chưa giao", ...t.unassigned }] : []),
          ].map((c) => (
            <button
              key={c.key || "all"}
              type="button"
              aria-pressed={tech === c.key}
              onClick={() => set("tech", c.key)}
              className={cn(
                "cursor-pointer rounded-md border bg-surface px-3 py-2 text-left hover:bg-surface-2",
                tech === c.key ? "border-primary ring-1 ring-primary" : "border-line",
              )}
            >
              <span className="block truncate text-sm font-medium">{c.name}</span>
              <span className="mt-0.5 flex items-baseline gap-3 text-sm whitespace-nowrap">
                <span><span className="text-lg font-semibold tabular">{c.open}</span> <span className="text-muted">đang chờ</span></span>
                {c.overdue > 0 && <span className="text-failed"><span className="font-semibold tabular">{c.overdue}</span> quá hạn</span>}
              </span>
            </button>
          ))}
        </nav>
      )}

      <div className="mb-3 flex flex-wrap items-end gap-3">
        <div role="group" aria-label="Lọc theo tiến độ" className="flex flex-wrap gap-1.5">
          {STATES.map((s) => (
            <Button key={s.key} size="sm" variant={state === s.key ? "primary" : "secondary"} aria-pressed={state === s.key} onClick={() => set("state", s.key === "open" ? "" : s.key)}>
              {s.label}
            </Button>
          ))}
        </div>
        <Field label="Tìm kiếm" className="ml-auto w-72">
          <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Số request, tiêu đề, người yêu cầu" />
        </Field>
      </div>

      {canAssign && <PendingReassignments />}
      {canAssign && selected.length > 0 && (
        <div role="region" aria-label="Thao tác với task đã chọn" className="mb-3 flex items-center gap-3 rounded-md border border-primary bg-primary-soft px-3 py-2 text-sm">
          <span>Đã chọn <strong className="tabular">{selected.length}</strong> task</span>
          <Button size="sm" variant="primary" onClick={() => setDialog(true)}>
            <ArrowRightLeft className="size-4" aria-hidden="true" />
            Chuyển việc
          </Button>
          <Button size="sm" variant="ghost" onClick={() => setSelected([])}>Bỏ chọn</Button>
        </div>
      )}

      {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
      {list.isLoading ? (
        <div className="rounded-md border border-line bg-surface"><LoadingRows rows={8} /></div>
      ) : (
        <DataTable
          data={items}
          columns={columns}
          caption="Task Helpdesk/Support"
          getRowId={(r) => r.id}
          empty={<EmptyState icon={ListChecks} title="Không có task phù hợp">Thử bộ lọc khác, hoặc đồng bộ lại từ ServiceDesk Plus.</EmptyState>}
        />
      )}
      {list.data && list.data.total > items.length && (
        <p className="mt-2 text-sm text-muted">Đang hiện {items.length}/{list.data.total} task. Thu hẹp bộ lọc để xem phần còn lại.</p>
      )}
      {dialog && <ReassignDialog ids={selected} onClose={() => setDialog(false)} onDone={() => { setDialog(false); setSelected([]); }} />}
    </>
  );
}
