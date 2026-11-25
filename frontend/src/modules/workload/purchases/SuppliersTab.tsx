import { useMemo, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { ColumnDef } from "@tanstack/react-table";
import { Building2, Pencil, Plus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDate } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { DataTable } from "@/core/ui/data-table";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono } from "@/core/ui/feedback";
import { Field, Input, Select, Switch, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import { useSuppliers, type Supplier } from "../api";

export const POSITION_LABEL = (n: number) => (n === 0 ? "Số tiền nhỏ nhất trên dòng" : `Số tiền thứ ${n} từ trái`);

function EditDialog({ item, onClose }: { item: Supplier | null; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(item?.name ?? "");
  const [keywords, setKeywords] = useState((item?.keywords ?? []).join("\n"));
  const [position, setPosition] = useState(item?.price_position ?? 0);
  const [website, setWebsite] = useState(item?.website ?? "");
  const [active, setActive] = useState(item?.active ?? true);
  const [tried, setTried] = useState(false);
  const kw = keywords.split("\n").map((k) => k.trim()).filter((k) => k.length >= 3);
  const bad = { name: name.trim().length < 2, kw: active && kw.length === 0 };
  const save = useMutation({
    mutationFn: () =>
      api<Supplier>(item ? `/api/workload/suppliers/${item.id}` : "/api/workload/suppliers", {
        method: item ? "PUT" : "POST",
        json: { name, keywords: kw, price_position: position, website, active },
      }),
    onSuccess: (x) => {
      toast.success(`Đã lưu nhà cung cấp “${x.name}”.`);
      void qc.invalidateQueries({ queryKey: ["workload", "suppliers"] });
      onClose();
    },
  });
  return (
    <Dialog
      open
      onClose={onClose}
      title={item ? `Sửa nhà cung cấp “${item.name}”` : "Thêm nhà cung cấp"}
      description="Dùng để tự nhận ra nhà cung cấp trong file báo giá PDF và đọc đúng cột đơn giá theo mẫu của họ."
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" loading={save.isPending} onClick={() => { setTried(true); if (!bad.name && !bad.kw) save.mutate(); }}>Lưu</Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-2">
        <Field label="Tên nhà cung cấp" required error="Nhập tên." showError={tried && bad.name} className="sm:col-span-2">
          <Input data-autofocus value={name} maxLength={200} onChange={(e) => setName(e.target.value)} placeholder="Công ty CP Thương mại FPT" />
        </Field>
        <Field
          label="Từ khoá nhận diện"
          required
          hint="Mỗi dòng một từ khoá có trên báo giá của họ, vd. mã số thuế, tên miền website, tên công ty viết liền. Chỉ cần khớp một từ khoá."
          error="Nhập ít nhất một từ khoá (≥ 3 ký tự)."
          showError={tried && bad.kw}
          className="sm:col-span-2"
        >
          <Textarea rows={3} value={keywords} onChange={(e) => setKeywords(e.target.value)} placeholder={"0101248141\nfptshop.com.vn"} />
        </Field>
        <Field label="Cột đơn giá trên dòng thiết bị" hint="Đếm các số tiền trên một dòng từ trái sang (bỏ qua số lượng nhỏ). Để mặc định nếu dòng chỉ có đơn giá và thành tiền.">
          <Select value={position} onChange={(e) => setPosition(Number(e.target.value))}>
            {[0, 1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{POSITION_LABEL(n)}</option>)}
          </Select>
        </Field>
        <Field label="Website (trang cửa hàng)" hint="Mở nhanh khi cần chụp màn hình không có hàng.">
          <Input type="url" value={website} maxLength={300} onChange={(e) => setWebsite(e.target.value)} placeholder="https://" />
        </Field>
        <div className="sm:col-span-2">
          <Switch checked={active} onChange={setActive} label="Đang hợp tác (dùng để nhận diện báo giá)" />
        </div>
      </div>
    </Dialog>
  );
}

export function SuppliersTab() {
  const { can } = useAuth();
  const manage = can("workload.catalog.manage");
  const q = useSuppliers();
  const [editing, setEditing] = useState<Supplier | null | "new">(null);
  const columns = useMemo<ColumnDef<Supplier, unknown>[]>(
    () => [
      {
        accessorKey: "name",
        header: "Nhà cung cấp",
        cell: (c) => (
          <span className="block">
            <span className="block font-medium">{c.row.original.name}</span>
            {c.row.original.website && (
              <a className="text-xs text-primary underline-offset-2 hover:underline" href={c.row.original.website} target="_blank" rel="noreferrer">{c.row.original.website}</a>
            )}
          </span>
        ),
      },
      { id: "keywords", header: "Từ khoá nhận diện", accessorFn: (x) => x.keywords.join(", "), cell: (c) => <Mono>{c.row.original.keywords.join(" · ") || "—"}</Mono> },
      { accessorKey: "price_position", header: "Cột đơn giá", cell: (c) => POSITION_LABEL(c.row.original.price_position) },
      { accessorKey: "updated_at", header: "Cập nhật", cell: (c) => <span className="text-xs text-muted">{c.row.original.updated_by ?? "—"}<br />{formatDate(c.row.original.updated_at)}</span> },
      {
        accessorKey: "active",
        header: "Trạng thái",
        cell: (c) => (c.row.original.active ? <StatusBadge status="done" label="Đang hợp tác" /> : <StatusBadge status="disabled" label="Ngừng" />),
      },
      ...(manage
        ? [
            {
              id: "edit",
              header: "Thao tác",
              enableSorting: false,
              cell: (c: { row: { original: Supplier } }) => (
                <Button size="sm" onClick={() => setEditing(c.row.original)}><Pencil className="size-4" aria-hidden="true" />Sửa</Button>
              ),
            } as ColumnDef<Supplier, unknown>,
          ]
        : []),
    ],
    [manage],
  );
  return (
    <>
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <p className="flex items-center gap-1 text-sm text-muted">
          Nhà cung cấp thường xuyên và mẫu báo giá của họ.
          <HelpTip title="Nhận diện báo giá">
            <span className="block">Khi tải file báo giá PDF (Bảng giá hoặc báo giá PC/Laptop của đề xuất), hệ thống tìm <b>từ khoá nhận diện</b> trong file để biết của nhà cung cấp nào.</span>
            <span className="block">Sau đó đọc đơn giá theo <b>cột đơn giá</b> của mẫu nhà cung cấp đó, vd. mẫu có “Giá niêm yết | Giá sau chiết khấu | Thành tiền” thì chọn “Số tiền thứ 2”.</span>
            <span className="block">Mã số thuế là từ khoá tốt nhất vì luôn có trên báo giá và không trùng.</span>
          </HelpTip>
        </p>
        {manage && (
          <Button className="ml-auto" variant="primary" onClick={() => setEditing("new")}><Plus className="size-4" aria-hidden="true" />Thêm nhà cung cấp</Button>
        )}
      </div>
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading ? (
        <LoadingRows rows={4} />
      ) : (
        <DataTable
          data={q.data ?? []}
          columns={columns}
          caption="Nhà cung cấp"
          getRowId={(x) => x.id}
          empty={<EmptyState icon={Building2} title="Chưa có nhà cung cấp">Thêm 4–5 nhà cung cấp thường xuyên để hệ thống tự nhận ra báo giá của họ.</EmptyState>}
        />
      )}
      {editing && <EditDialog item={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}
