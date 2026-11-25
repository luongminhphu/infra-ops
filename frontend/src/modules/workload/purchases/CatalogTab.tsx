import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { FileSearch, FileText, Pencil, Plus, Tags } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDate, formatNumber, formatVnd } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { DateInput } from "@/core/ui/date-input";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows } from "@/core/ui/feedback";
import { Field, Input, Switch, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import { useCatalog, type CatalogItem } from "../api";
import { PdfImportDialog } from "./PdfImportDialog";

const digits = (s: string) => s.replace(/\D/g, "");
const group = (s: string) => (s ? formatNumber(Number(s)) : "");

function EditDialog({ item, onClose }: { item: CatalogItem | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(item?.name ?? "");
  const [category, setCategory] = useState(item?.category ?? "");
  const [spec, setSpec] = useState(item?.spec ?? "");
  const [price, setPrice] = useState(item?.unit_price ? String(item.unit_price) : "");
  const [vendor, setVendor] = useState(item?.vendor ?? "");
  const [sdpProduct, setSdpProduct] = useState(item?.sdp_product ?? "");
  const [quoted, setQuoted] = useState(item?.quoted_at ?? "");
  const [quotedBad, setQuotedBad] = useState(false);
  const [active, setActive] = useState(item?.active ?? true);
  const [tried, setTried] = useState(false);
  const bad = { name: name.trim().length < 2, quoted: quotedBad };
  const save = useMutation({
    mutationFn: () =>
      api<CatalogItem>(item ? `/api/workload/catalog/${item.id}` : "/api/workload/catalog", {
        method: item ? "PUT" : "POST",
        json: { name, category, spec, unit_price: price ? Number(price) : null, sdp_product: sdpProduct, vendor, quoted_at: quoted || null, active },
      }),
    onSuccess: (c) => {
      toast.success(`Đã lưu “${c.name}”${c.unit_price ? ` – ${formatVnd(c.unit_price)}` : ""}. PO nháp đã được tính lại.`);
      void qc.invalidateQueries({ queryKey: ["workload"] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onClose={onClose}
      title={item ? `Cập nhật giá “${item.name}”` : "Thêm thiết bị vào bảng giá"}
      description="Nhập theo báo giá mới nhất của đối tác. Giá mới áp dụng cho các PO chưa chốt."
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" loading={save.isPending} onClick={() => { setTried(true); if (!bad.name && !bad.quoted) save.mutate(); }}>Lưu</Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Tên thiết bị" required error="Nhập tên thiết bị." showError={tried && bad.name} className="sm:col-span-2">
          <Input data-autofocus={!item} value={name} maxLength={300} onChange={(e) => setName(e.target.value)} placeholder="Laptop Dell Latitude 5440 i5/16GB/512GB" />
        </Field>
        <Field label="Nhóm" hint="Ví dụ: Laptop, Màn hình, Linh kiện">
          <Input value={category} maxLength={100} onChange={(e) => setCategory(e.target.value)} />
        </Field>
        <Field label="Đơn giá (đ, gồm VAT)" hint={price ? formatVnd(Number(price)) : "Để trống nếu chưa có báo giá"}>
          <Input data-autofocus={!!item} inputMode="numeric" value={group(price)} onChange={(e) => setPrice(digits(e.target.value).slice(0, 11))} />
        </Field>
        <Field label="Đối tác báo giá">
          <Input value={vendor} maxLength={200} onChange={(e) => setVendor(e.target.value)} />
        </Field>
        <Field label="Ngày báo giá" error="Ngày theo dạng dd/MM/yyyy." showError={tried && bad.quoted}>
          <DateInput value={quoted} onChange={setQuoted} onInvalidChange={setQuotedBad} />
        </Field>
        <Field
          label="Sản phẩm trên ServiceDesk Plus"
          hint="Tên Product trên SDP (vd. Dell Latitude 5440). Có tên thì khi nhận hàng hệ thống tạo tài sản In Store theo serial; để trống với linh kiện, sửa chữa không quản lý tài sản."
          className="sm:col-span-2"
        >
          <Input value={sdpProduct} maxLength={300} onChange={(e) => setSdpProduct(e.target.value)} />
        </Field>
        <Field label="Quy cách" className="sm:col-span-2">
          <Textarea rows={2} maxLength={1000} value={spec} onChange={(e) => setSpec(e.target.value)} />
        </Field>
        <div className="sm:col-span-2">
          <Switch checked={active} onChange={setActive} label="Đang dùng (ẩn khỏi danh sách chọn khi tắt)" />
        </div>
      </div>
    </Dialog>
  );
}

export function CatalogTab() {
  const { can } = useAuth();
  const manage = can("workload.catalog.manage");
  const q = useCatalog();
  const [editing, setEditing] = useState<CatalogItem | null | "new">(null);
  const [importing, setImporting] = useState(false);
  const columns = useMemo<ColumnDef<CatalogItem, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Thiết bị",
        cell: (c) => (
          <span className="block max-w-96">
            <span className="block font-medium">{c.row.original.name}</span>
            {c.row.original.spec && <span className="block truncate text-xs text-muted" title={c.row.original.spec}>{c.row.original.spec}</span>}
            <span className="block text-xs text-muted">{c.row.original.sdp_product ? `Tài sản SDP: ${c.row.original.sdp_product}` : "Không tạo tài sản SDP"}</span>
          </span>
        ),
      },
      { accessorKey: "category", header: "Nhóm" },
      {
        accessorKey: "unit_price",
        header: "Đơn giá",
        cell: (c) =>
          c.row.original.needs_quotes ? (
            <span className="text-sm text-muted">Theo báo giá từng đề xuất</span>
          ) : (
            <span className="block whitespace-nowrap">
              <span className="tabular">{formatVnd(c.row.original.unit_price, "Chưa có giá")}</span>
              {c.row.original.quote_file && (
                <a className="mt-0.5 flex items-center gap-1 text-xs text-primary underline-offset-2 hover:underline" href={`/api/workload/catalog/${c.row.original.id}/quote-file`} target="_blank" rel="noreferrer">
                  <FileText className="size-3.5" aria-hidden="true" />File báo giá
                </a>
              )}
            </span>
          ),
      },
      { accessorKey: "vendor", header: "Đối tác", cell: (c) => c.row.original.vendor || "—" },
      { accessorKey: "quoted_at", header: "Ngày báo giá", cell: (c) => <span className="tabular">{formatDate(c.row.original.quoted_at)}</span> },
      { accessorKey: "updated_by", header: "Cập nhật", cell: (c) => <span className="text-xs text-muted">{c.row.original.updated_by ?? "—"}<br />{formatDate(c.row.original.updated_at)}</span> },
      {
        accessorKey: "active",
        header: "Trạng thái",
        cell: (c) => (c.row.original.active ? <StatusBadge status="done" label="Đang dùng" /> : <StatusBadge status="disabled" label="Ngừng dùng" />),
      },
      ...(manage
        ? [
            {
              id: "edit",
              header: "Thao tác",
              enableSorting: false,
              cell: (c: { row: { original: CatalogItem } }) => (
                <Button size="sm" onClick={() => setEditing(c.row.original)}><Pencil className="size-4" aria-hidden="true" />Sửa</Button>
              ),
            } as ColumnDef<CatalogItem, unknown>,
          ]
        : []),
    ],
    [manage],
  );
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <p className="flex items-center gap-1 text-sm text-muted">
          Đơn giá dùng để tính giá trị PO.
          <HelpTip title="Cập nhật bảng giá">
            <span className="block">Có file báo giá PDF của đối tác: bấm <b>Cập nhật giá từ PDF</b>, hệ thống đọc file và gợi ý giá từng thiết bị để bạn xác nhận. Hoặc bấm <b>Sửa</b> để nhập giá tay.</span>
            <span className="block">Nhóm PC/Laptop không dùng giá ở đây: mỗi đề xuất được định giá bằng báo giá riêng (trên 20 triệu cần 3 báo giá, lấy giá thấp nhất).</span>
            <span className="block">Giá mới áp dụng ngay cho các PO nháp (hệ thống gom lại theo hạn mức). PO đã chốt giữ giá lúc chốt.</span>
            <span className="block">Thiết bị ngừng dùng thì tắt “Đang dùng” thay vì xoá, để lịch sử đề xuất vẫn đúng.</span>
          </HelpTip>
        </p>
        {manage && (
          <span className="ml-auto flex flex-wrap gap-2">
            <Button onClick={() => setImporting(true)}><FileSearch className="size-4" aria-hidden="true" />Cập nhật giá từ PDF</Button>
            <Button variant="primary" onClick={() => setEditing("new")}><Plus className="size-4" aria-hidden="true" />Thêm thiết bị</Button>
          </span>
        )}
      </div>
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading ? (
        <LoadingRows rows={5} />
      ) : (
        <DataTable
          data={q.data ?? []}
          columns={columns}
          caption="Bảng giá thiết bị"
          getRowId={(c) => c.id}
          empty={<EmptyState icon={Tags} title="Chưa có thiết bị trong bảng giá">{manage ? "Bấm “Thêm thiết bị” và nhập đơn giá theo báo giá của đối tác." : "Supervisor sẽ nhập bảng giá sau khi có báo giá."}</EmptyState>}
        />
      )}
      {importing && <PdfImportDialog onClose={() => setImporting(false)} />}
      {editing && <EditDialog item={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}
