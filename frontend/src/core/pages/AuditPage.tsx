import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useInfiniteQuery, useMutation, useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { ScrollText, ShieldCheck, X } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/core/api/client";
import type { AuditEntry } from "@/core/api/types";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { DateInput } from "@/core/ui/date-input";
import { StatusBadge, type Status } from "@/core/ui/status";
import { formatDateTime } from "@/core/lib/format";
import { useDebounced } from "@/core/lib/useDebounced";

export const ACTION_LABEL: Record<string, string> = {
  "auth.login": "Đăng nhập",
  "auth.logout": "Đăng xuất",
  "auth.password_changed": "Đổi mật khẩu",
  "connection.created": "Thêm kết nối",
  "connection.updated": "Sửa kết nối",
  "connection.deleted": "Xoá kết nối",
  "connection.imported": "Nhập cấu hình kết nối",
  "connection.exported": "Xuất cấu hình kết nối",
  "connection.credential_set": "Nhập credential",
  "connection.credential_rotated": "Thay mới credential",
  "connection.credential_removed": "Xoá credential",
  "connection.tested": "Kiểm tra kết nối",
  "rbac.role_created": "Tạo vai trò",
  "rbac.role_updated": "Sửa vai trò",
  "rbac.role_deleted": "Xoá vai trò",
  "rbac.user_created": "Thêm người dùng",
  "rbac.user_updated": "Sửa người dùng",
  "settings.updated": "Sửa cấu hình module",
  "documents.created": "Lập biên bản",
  "documents.finalized": "Chốt biên bản",
  "documents.signed_uploaded": "Tải bản ký",
  "documents.voided": "Huỷ biên bản",
  "documents.sdp_attach": "Đính kèm biên bản vào SDP",
  "documents.template_uploaded": "Tải mẫu biên bản",
  "documents.template_activated": "Dùng phiên bản mẫu",
  "lifecycle.sync": "Đồng bộ SDP/BioStar",
  "lifecycle.sync_requested": "Yêu cầu đồng bộ ngay",
  "lifecycle.issue_acknowledged": "Đánh dấu lệch đã biết",
  "lifecycle.issue_reopened": "Mở lại lệch",
  "lifecycle.reconciliation_exported": "Xuất danh sách đối soát",
  "lifecycle.attendance_exported": "Xuất báo cáo chấm công",
  "lifecycle.offboard_review_exported": "Xuất rà soát nghỉ việc gửi HR",
  "workload.tickets_exported": "Xuất Excel task Infra Support",
  "playbook.guide_created": "Tạo hướng dẫn Playbook",
  "playbook.draft_saved": "Lưu nháp hướng dẫn Playbook",
  "playbook.guide_published": "Xuất bản hướng dẫn Playbook",
  "playbook.guide_deleted": "Xoá hướng dẫn Playbook",
  "playbook.image_added": "Thêm ảnh vào Playbook",
  "workflow.job_created": "Tạo Job",
  "workflow.job_started": "Bắt đầu Job",
  "workflow.job_params_updated": "Sửa dữ liệu Job",
  "workflow.job_cancelled": "Huỷ Job",
  "workflow.job_override_closed": "Buộc đóng Job",
  "workflow.step_executed": "Chạy bước tự động",
  "workflow.step_completed": "Hoàn tất bước thủ công",
  "workflow.step_confirmed": "Xác nhận chạy bước",
  "workflow.step_verified": "Kiểm tra bước: đạt",
  "workflow.step_rejected": "Kiểm tra bước: không đạt",
  "workflow.step_failed": "Bước bị lỗi",
  "workflow.step_retried": "Chạy lại bước",
  "workflow.step_skipped": "Bỏ qua bước",
  "workflow.evidence_added": "Thêm bằng chứng",
  "workflow.evidence_removed": "Xoá bằng chứng",
  "workflow.checklist_saved": "Lưu phiên bản checklist",
  "workload.sync": "Đồng bộ task SDP",
  "workload.sync_requested": "Yêu cầu đồng bộ task",
  "workload.reassign_requested": "Chuyển việc",
  "workload.technician_linked": "Liên kết Operator ↔ technician",
  "workload.technician_link_reset": "Liên kết lại theo e-mail",
};

const MODULE_LABEL: Record<string, string> = { core: "Hệ thống", lifecycle: "Vòng đời nhân sự", workload: "Công việc Infra Support" };
const RESULT: Record<AuditEntry["result"], { status: Status; label: string }> = {
  success: { status: "done", label: "Thành công" },
  failure: { status: "failed", label: "Thất bại" },
  denied: { status: "warning", label: "Bị từ chối" },
};

interface Page {
  items: AuditEntry[];
  next_before_id: number | null;
}

function JsonBlock({ label, value }: { label: string; value: Record<string, unknown> | null }) {
  if (!value || Object.keys(value).length === 0) return null;
  return (
    <div>
      <h3 className="mb-1 text-sm font-semibold">{label}</h3>
      <pre className="overflow-x-auto rounded-sm bg-surface-2 p-2 font-mono text-xs leading-relaxed">{JSON.stringify(value, null, 2)}</pre>
    </div>
  );
}

function Detail({ entry, onClose }: { entry: AuditEntry; onClose: () => void }) {
  return (
    <aside aria-label="Chi tiết bản ghi" className="sticky top-[calc(var(--topbar-h)+1.25rem)] max-h-[calc(100dvh-110px)] overflow-y-auto rounded-md border border-line bg-surface">
      <header className="flex items-start justify-between gap-2 border-b border-line px-4 py-3">
        <div>
          <h2 className="text-lg font-semibold">{ACTION_LABEL[entry.action] ?? entry.action}</h2>
          <p className="text-sm text-muted">#{entry.id} · {formatDateTime(entry.at)}</p>
        </div>
        <Button variant="ghost" size="icon" aria-label="Đóng chi tiết" onClick={onClose}>
          <X className="size-4" aria-hidden="true" />
        </Button>
      </header>
      <div className="space-y-4 p-4">
        <dl className="grid grid-cols-[max-content_1fr] gap-x-4 gap-y-1.5 text-sm">
          <dt className="text-muted">Kết quả</dt>
          <dd><StatusBadge status={RESULT[entry.result].status} label={RESULT[entry.result].label} /></dd>
          <dt className="text-muted">Người thực hiện</dt>
          <dd className="break-all">{entry.actor_upn ?? "—"}</dd>
          <dt className="text-muted">Địa chỉ IP</dt>
          <dd><Mono>{entry.actor_ip ?? "—"}</Mono></dd>
          <dt className="text-muted">Module</dt>
          <dd>{MODULE_LABEL[entry.module_id] ?? entry.module_id}</dd>
          {entry.system_type && (<><dt className="text-muted">Hệ thống</dt><dd>{entry.system_type}</dd></>)}
          {entry.target_label && (<><dt className="text-muted">Đối tượng</dt><dd>{entry.target_label}</dd></>)}
          {entry.message && (<><dt className="text-muted">Ghi chú</dt><dd>{entry.message}</dd></>)}
          {entry.correlation_id && (<><dt className="text-muted">Mã yêu cầu</dt><dd><Mono>{entry.correlation_id}</Mono></dd></>)}
        </dl>
        <JsonBlock label="Giá trị trước" value={entry.before} />
        <JsonBlock label="Giá trị sau" value={entry.after} />
      </div>
    </aside>
  );
}

export function AuditPage() {
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<AuditEntry | null>(null);
  const filters = {
    q: params.get("q") ?? "",
    module: params.get("module") ?? "",
    action: params.get("action") ?? "",
    result: params.get("result") ?? "",
    from: params.get("from") ?? "",
    to: params.get("to") ?? "",
    target_id: params.get("target_id") ?? "",
  };
  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };

  // Debounce free-text search so typing does not fire a request per keystroke.
  const [search, setSearch] = useState(filters.q);
  const debouncedSearch = useDebounced(search, 350);
  useEffect(() => {
    if (debouncedSearch !== filters.q) setFilter("q", debouncedSearch);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debouncedSearch]);

  const facets = useQuery({ queryKey: ["audit-facets"], queryFn: () => api<{ modules: string[]; actions: string[] }>("/api/core/audit/facets") });
  const query = useInfiniteQuery({
    queryKey: ["audit", filters],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) => {
      const qs = new URLSearchParams({ limit: "100" });
      for (const [k, v] of Object.entries(filters)) {
        if (!v) continue;
        if (k === "from") qs.set("from", `${v}T00:00:00+07:00`);
        else if (k === "to") qs.set("to", `${v}T23:59:59+07:00`);
        else qs.set(k, v);
      }
      if (pageParam) qs.set("before_id", String(pageParam));
      return api<Page>(`/api/core/audit?${qs}`);
    },
    getNextPageParam: (last) => last.next_before_id,
  });
  const rows = useMemo(() => query.data?.pages.flatMap((p) => p.items) ?? [], [query.data]);

  const verify = useMutation({
    mutationFn: () => api<{ ok: boolean; checked: number; broken_at_id: number | null; reason: string | null }>("/api/core/audit/verify"),
    onSuccess: (r) =>
      r.ok
        ? toast.success(`Nhật ký toàn vẹn: đã kiểm tra ${r.checked.toLocaleString("vi-VN")} bản ghi, không phát hiện chỉnh sửa.`)
        : toast.error(`Phát hiện bất thường tại bản ghi #${r.broken_at_id}: ${r.reason}`),
  });

  const columns = useMemo<ColumnDef<AuditEntry, unknown>[]>(
    () => [
      { accessorKey: "at", header: "Thời gian", cell: (c) => <span className="tabular whitespace-nowrap">{formatDateTime(c.getValue() as string)}</span>, size: 140 },
      { accessorKey: "actor_upn", header: "Người thực hiện", cell: (c) => <span className="block max-w-48 truncate">{(c.getValue() as string) ?? "—"}</span> },
      { accessorKey: "module_id", header: "Module", cell: (c) => <span className="whitespace-nowrap">{MODULE_LABEL[c.getValue() as string] ?? (c.getValue() as string)}</span> },
      {
        accessorKey: "action",
        header: "Hành động",
        cell: (c) => (
          <button type="button" className="cursor-pointer text-left font-medium whitespace-nowrap hover:text-primary hover:underline" onClick={() => setSelected(c.row.original)}>
            {ACTION_LABEL[c.getValue() as string] ?? (c.getValue() as string)}
          </button>
        ),
      },
      { accessorKey: "target_label", header: "Đối tượng", cell: (c) => <span className="block max-w-72 truncate" title={(c.getValue() as string) ?? undefined}>{(c.getValue() as string) ?? "—"}</span> },
      {
        accessorKey: "result",
        header: "Kết quả",
        cell: (c) => {
          const r = RESULT[c.getValue() as AuditEntry["result"]];
          return <StatusBadge status={r.status} label={r.label} />;
        },
      },
    ],
    [],
  );

  const activeFilters = Object.values(filters).some(Boolean);

  return (
    <>
      <PageHeader
        title="Nhật ký kiểm tra"
        description="Mọi thay đổi cấu hình, đăng nhập và thao tác trên hệ thống. Nhật ký chỉ ghi thêm, không sửa hoặc xoá được; secret luôn được che."
        actions={
          <Button onClick={() => verify.mutate()} loading={verify.isPending}>
            <ShieldCheck className="size-4" aria-hidden="true" />
            Kiểm tra toàn vẹn
          </Button>
        }
      />
      <form className="mb-3 grid gap-3 sm:grid-cols-2 lg:grid-cols-6" onSubmit={(e) => e.preventDefault()} role="search" aria-label="Lọc nhật ký">
        <Field label="Tìm kiếm" className="lg:col-span-2">
          <Input type="search" value={search} placeholder="Đối tượng, hành động, ghi chú" onChange={(e) => setSearch(e.target.value)} />
        </Field>
        <Field label="Module">
          <Select value={filters.module} onChange={(e) => setFilter("module", e.target.value)}>
            <option value="">Tất cả</option>
            {facets.data?.modules.map((m) => <option key={m} value={m}>{MODULE_LABEL[m] ?? m}</option>)}
          </Select>
        </Field>
        <Field label="Hành động">
          <Select value={filters.action} onChange={(e) => setFilter("action", e.target.value)}>
            <option value="">Tất cả</option>
            {facets.data?.actions.map((a) => <option key={a} value={a}>{ACTION_LABEL[a] ?? a}</option>)}
          </Select>
        </Field>
        <Field label="Từ ngày">
          <DateInput value={filters.from} onChange={(v) => setFilter("from", v)} className="w-full tabular" />
        </Field>
        <Field label="Đến ngày">
          <DateInput value={filters.to} onChange={(v) => setFilter("to", v)} className="w-full tabular" />
        </Field>
      </form>
      {activeFilters && (
        <p className="mb-3 text-sm text-muted">
          {filters.target_id && "Đang xem lịch sử của một đối tượng. "}
          <Button variant="link" size="sm" onClick={() => { setSearch(""); setParams({}, { replace: true }); }}>Xoá bộ lọc</Button>
        </p>
      )}
      {query.isError && <ErrorState error={query.error} onRetry={() => query.refetch()} />}
      <div className={selected ? "grid gap-4 xl:grid-cols-[minmax(0,1fr)_400px]" : undefined}>
        <div>
          {query.isLoading ? (
            <div className="rounded-md border border-line bg-surface"><LoadingRows rows={8} label="Đang tải nhật ký" /></div>
          ) : (
            <DataTable
              data={rows}
              columns={columns}
              caption="Nhật ký kiểm tra"
              getRowId={(r) => String(r.id)}
              onRowClick={setSelected}
              selectedId={selected ? String(selected.id) : null}
              empty={<EmptyState icon={ScrollText} title="Không có bản ghi phù hợp">Thử bỏ bớt bộ lọc hoặc mở rộng khoảng thời gian.</EmptyState>}
            />
          )}
          {query.hasNextPage && (
            <div className="mt-3 flex justify-center">
              <Button onClick={() => query.fetchNextPage()} loading={query.isFetchingNextPage}>Tải thêm</Button>
            </div>
          )}
        </div>
        {selected && <Detail entry={selected} onClose={() => setSelected(null)} />}
      </div>
    </>
  );
}
