import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { ArrowRightLeft, Download, ListChecks, RefreshCw } from "lucide-react";
import { toast } from "sonner";
import { useSearchParams } from "react-router-dom";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input, Select, Textarea } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { formatDateTime, formatRelative } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import { useDebounced } from "@/core/lib/useDebounced";
import { useSyncNow, useSyncStatus, useTechnicians, type Ticket, type TicketState } from "../api";
import { USUAL_SDP_STATUS, ticketBadge } from "../labels";

const STATES: { key: TicketState; label: string }[] = [
  { key: "open", label: "Tất cả đang mở" },
  { key: "in_progress", label: "Đang thực hiện" },
  { key: "pending", label: "Chưa bắt đầu" },
  { key: "on_hold", label: "Tạm dừng" },
  { key: "overdue", label: "Quá hạn" },
  { key: "done", label: "Đã thực hiện" },
  { key: "cancelled", label: "Đã huỷ" },
  { key: "all", label: "Tất cả" },
];


function ReassignDialog({ tickets, onClose, onDone }: { tickets: Ticket[]; onClose: () => void; onDone: () => void }) {
  const techs = useTechnicians();
  const qc = useQueryClient();
  const [to, setTo] = useState("");
  const [reason, setReason] = useState("");
  const [touched, setTouched] = useState(false);
  const submit = useMutation({
    mutationFn: () =>
      api<{ id: string }>("/api/workload/tickets/reassign", {
        method: "POST",
        json: { ticket_ids: tickets.map((t) => t.id), to_technician_id: to, reason },
      }),
    onSuccess: () => {
      toast.success(`Đang chuyển ${numbers} cho ${target?.name}. infra-ops cập nhật Technician trên SDP trong ít giây.`);
      void qc.invalidateQueries({ queryKey: ["workload"] });
      onDone();
    },
  });
  const options = (techs.data?.items ?? []).filter((t) => t.active);
  const target = options.find((t) => t.id === to);
  const numbers = tickets.map((t) => `#${t.display_id ?? t.id}`).join(", ");
  const unlinked = options.some((t) => !t.user);
  return (
    <Dialog
      open
      onClose={onClose}
      title={`Chuyển ${tickets.length} task`}
      description="Technician của các request dưới đây được đổi trực tiếp trên ServiceDesk Plus và ghi nhật ký kiểm tra. Không có thao tác hoàn tác tự động."
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
            {target ? `Chuyển ${tickets.length} task cho ${target.name}` : "Chuyển việc"}
          </Button>
        </>
      }
    >
      <div className="space-y-3">
        <div>
          <p className="mb-1 text-sm font-medium">Task sẽ chuyển</p>
          <ul className="max-h-44 divide-y divide-line overflow-y-auto rounded-sm border border-line text-sm" tabIndex={0} aria-label="Task sẽ chuyển">
            {tickets.map((t) => (
              <li key={t.id} className="flex items-baseline gap-2 px-2.5 py-1.5">
                <Mono className="shrink-0">#{t.display_id ?? t.id}</Mono>
                <span className="min-w-0 flex-1 truncate" title={t.subject}>{t.subject}</span>
                <span className="shrink-0 text-muted">{t.technician_name ?? "Chưa giao"}</span>
              </li>
            ))}
          </ul>
        </div>
        <Field
          label="Chuyển cho"
          required
          hint={unlinked ? "Technician chưa liên kết tài khoản infra-ops vẫn nhận được việc trên SDP, nhưng chưa thấy task này trong infra-ops." : undefined} error="Chọn người nhận việc." showError={touched && !to}>
          <Select value={to} onChange={(e) => setTo(e.target.value)} onBlur={() => setTouched(true)}>
            <option value="">— Chọn Operator —</option>
            {options.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} · đang có {t.open} task
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
  });
  const cancel = useMutation({
    mutationFn: (id: string) => api(`/api/core/jobs/${id}/cancel`, { method: "POST", json: { reason: "Manager bỏ yêu cầu chuyển việc lỗi" } }),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["workload"] }),
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
  // ?q= lets other pages (dashboard overdue rows) open the list on one request.
  const [q, setQ] = useState(params.get("q") ?? "");
  const debounced = useDebounced(q);
  const [selected, setSelected] = useState<string[]>([]);
  const [dialog, setDialog] = useState(false);
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
      { accessorKey: "subject", header: "Tiêu đề", cell: (c) => <span className="block max-w-80 truncate" title={c.row.original.subject}>{c.row.original.subject}</span> },
      { accessorKey: "requester_name", header: "Người yêu cầu", cell: (c) => c.row.original.requester_name ?? "—" },
      { accessorKey: "technician_name", header: "Người xử lý", cell: (c) => <span className="whitespace-nowrap">{c.row.original.technician_name ?? <span className="text-muted">Chưa giao</span>}</span> },
      {
        id: "sla",
        header: "Tiến độ",
        cell: (c) => (
          <span className="block whitespace-nowrap">
            <StatusBadge {...ticketBadge(c.row.original)} />
            {!USUAL_SDP_STATUS.has(c.row.original.status.toLowerCase()) && (
              <span className="mt-0.5 block text-xs text-muted">SDP: {c.row.original.status || "—"}</span>
            )}
          </span>
        ),
      },
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

  const [exporting, setExporting] = useState(false);
  const download = async () => {
    setExporting(true);
    try {
      const qs = new URLSearchParams({ state, technician_id: tech, q: debounced });
      const res = await fetch(`/api/workload/tickets/export?${qs}`, { credentials: "same-origin" });
      if (!res.ok) throw new Error("Không xuất được file. Thử lại sau.");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await res.blob());
      a.download = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? "task-infra-support.xlsx";
      a.click();
      URL.revokeObjectURL(a.href);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setExporting(false);
    }
  };

  const t = techs.data;
  return (
    <>
      <PageHeader
        title="Công việc Infra Support"
        description={
          <>
            Task Infra Support trên ServiceDesk Plus theo từng Operator. Đồng bộ gần nhất:{" "}
            {formatRelative(sync.data?.last_synced_at).toLowerCase()}.
            {sync.data?.task?.last_error && <span className="block text-failed">Lần đồng bộ gần nhất lỗi: {sync.data.task.last_error}</span>}
          </>
        }
        actions={
          <span className="inline-flex flex-wrap gap-2">
            <Button
              onClick={download}
              loading={exporting}
              title="Bảng tổng hợp task tồn theo người xử lý và danh sách task theo bộ lọc đang chọn"
            >
              <Download className="size-4" aria-hidden="true" />
              Xuất Excel
            </Button>
            {can("workload.sync.run") && (
              <Button onClick={() => syncNow.mutate()} loading={syncNow.isPending || sync.data?.task?.running}>
                <RefreshCw className="size-4" aria-hidden="true" />
                Đồng bộ ngay
              </Button>
            )}
          </span>
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
          caption="Task Infra Support"
          getRowId={(r) => r.id}
          empty={<EmptyState icon={ListChecks} title="Không có task phù hợp">Thử bộ lọc khác, hoặc đồng bộ lại từ ServiceDesk Plus.</EmptyState>}
        />
      )}
      {list.data && list.data.total > items.length && (
        <p className="mt-2 text-sm text-muted">Đang hiện {items.length}/{list.data.total} task. Thu hẹp bộ lọc để xem phần còn lại.</p>
      )}
      {dialog && <ReassignDialog tickets={items.filter((t) => selected.includes(t.id))} onClose={() => setDialog(false)} onDone={() => { setDialog(false); setSelected([]); }} />}
    </>
  );
}
