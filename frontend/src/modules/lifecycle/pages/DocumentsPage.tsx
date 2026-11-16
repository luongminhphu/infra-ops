import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { FilePlus2, FileText } from "lucide-react";
import { api } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { DOC_STATUS, KIND_SHORT_TITLE, SHORT_OF, useKinds, type Doc, type DocStatus } from "../docs";

export function DocumentsPage() {
  const navigate = useNavigate();
  const kinds = useKinds();
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  const [kind, setKind] = useState("");
  const [status, setStatus] = useState<"" | DocStatus>("");
  useEffect(() => {
    const t = window.setTimeout(() => setDebounced(q), 300);
    return () => window.clearTimeout(t);
  }, [q]);
  const list = useQuery({
    queryKey: ["documents", debounced, kind, status],
    queryFn: () => api<{ total: number; items: Doc[] }>(`/api/core/documents?${new URLSearchParams({ q: debounced, kind, status, limit: "500" })}`),
  });
  const canCreate = kinds.data?.some((k) => k.can_create);
  const columns = useMemo<ColumnDef<Doc, unknown>[]>(
    () => [
      {
        accessorKey: "number",
        header: "Số biên bản",
        cell: (c) => (
          <Link to={`/lifecycle/documents/${c.row.original.id}`} className="font-medium hover:text-primary hover:underline">
            {c.row.original.number ? <Mono>{c.row.original.number}</Mono> : "Bản nháp"}
          </Link>
        ),
      },
      { accessorKey: "kind", header: "Loại", cell: (c) => KIND_SHORT_TITLE[SHORT_OF[c.row.original.kind] ?? "handover"] },
      { accessorKey: "subject_label", header: "Nhân viên", cell: (c) => <span className="whitespace-nowrap">{c.row.original.subject_label}</span> },
      { id: "date", header: "Ngày bàn giao / thu hồi", accessorFn: (d) => d.data.date, cell: (c) => <span className="tabular">{formatDate(c.row.original.data.date)}</span> },
      { accessorKey: "external_ref", header: "Request SDP", cell: (c) => (c.row.original.external_ref ? <Mono>#{c.row.original.external_ref}</Mono> : <span className="text-muted">—</span>) },
      { accessorKey: "created_at", header: "Lập lúc", cell: (c) => <span className="tabular whitespace-nowrap text-muted">{formatDateTime(c.row.original.created_at)}</span> },
      { accessorKey: "status", header: "Trạng thái", cell: (c) => <StatusBadge status={DOC_STATUS[c.row.original.status].status} label={DOC_STATUS[c.row.original.status].label} /> },
    ],
    [],
  );
  return (
    <>
      <PageHeader
        title="Biên bản bàn giao / thu hồi"
        description="Lập biên bản từ dữ liệu SDP, xem trước, chốt để cấp số, in cho các bên ký rồi tải bản đã ký lên."
        actions={
          canCreate && (
            <>
              <Button onClick={() => navigate("/lifecycle/documents/new?kind=return")}>
                <FilePlus2 className="size-4" aria-hidden="true" />
                Lập biên bản thu hồi
              </Button>
              <Button variant="primary" onClick={() => navigate("/lifecycle/documents/new?kind=handover")}>
                <FilePlus2 className="size-4" aria-hidden="true" />
                Lập biên bản bàn giao
              </Button>
            </>
          )
        }
      />
      <form role="search" aria-label="Lọc biên bản" className="mb-3 grid gap-3 sm:grid-cols-[2fr_1fr_1fr]" onSubmit={(e) => e.preventDefault()}>
        <Field label="Tìm kiếm">
          <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Số biên bản, Mã NV, họ tên" />
        </Field>
        <Field label="Loại">
          <Select value={kind} onChange={(e) => setKind(e.target.value)}>
            <option value="">Tất cả</option>
            <option value="lifecycle.handover">Bàn giao</option>
            <option value="lifecycle.return">Thu hồi</option>
          </Select>
        </Field>
        <Field label="Trạng thái">
          <Select value={status} onChange={(e) => setStatus(e.target.value as DocStatus | "")}>
            <option value="">Tất cả</option>
            {Object.entries(DOC_STATUS).map(([k, v]) => <option key={k} value={k}>{v.label}</option>)}
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
          caption="Biên bản"
          getRowId={(d) => d.id}
          onRowClick={(d) => navigate(`/lifecycle/documents/${d.id}`)}
          empty={
            <EmptyState icon={FileText} title="Chưa có biên bản nào">
              Lập biên bản bàn giao khi cấp thiết bị cho nhân viên, hoặc biên bản thu hồi khi nhân viên trả thiết bị.
            </EmptyState>
          }
        />
      )}
    </>
  );
}
