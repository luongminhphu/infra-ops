import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { BadgeCheck, ExternalLink, FileText, Image, PackageX, Paperclip, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/core/api/client";
import { formatNumber, formatVnd } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { ConfirmDialog } from "@/core/ui/confirm-dialog";
import { Field, Input } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { useSuppliers, type Purchase } from "../api";
import { pastedImage } from "../evidence";

const digits = (s: string) => s.replace(/\D/g, "").slice(0, 11);
const group = (s: string) => (s ? formatNumber(Number(s)) : "");

interface ReadResult {
  file_id: string;
  filename: string;
  supplier: { id: string; name: string } | null;
  unit_price: number | null;
  line: string | null;
}

/**
 * Vendor quotes of one proposal (PC/Laptop). A quote PDF is read on upload: the vendor is recognised
 * from its keywords and the price from its form; a vendor without stock is recorded with a screenshot.
 */
export function QuotesBlock({ p, onChange }: { p: Purchase; onChange: (p: Purchase) => void }) {
  const suppliers = useSuppliers();
  const [mode, setMode] = useState<"quote" | "stock">("quote");
  const [vendor, setVendor] = useState("");
  const [price, setPrice] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [read, setRead] = useState<ReadResult | null>(null);
  const [tried, setTried] = useState(false);
  const editable = p.status === "proposed";
  const supplier = suppliers.data?.find((s) => s.name.toLowerCase() === vendor.trim().toLowerCase());
  const reset = () => {
    setVendor("");
    setPrice("");
    setFile(null);
    setRead(null);
    setTried(false);
  };
  const readPdf = useMutation({
    mutationFn: (f: File) => {
      const fd = new FormData();
      fd.append("file", f);
      return api<ReadResult>(`/api/workload/purchases/${p.id}/quotes/read`, { method: "POST", body: fd });
    },
    onSuccess: (r) => {
      setRead(r);
      if (r.supplier) setVendor(r.supplier.name);
      if (r.unit_price) setPrice(String(r.unit_price));
      toast.message(
        r.supplier
          ? `Nhận diện: ${r.supplier.name}${r.unit_price ? ` – đơn giá ${formatVnd(r.unit_price)}` : " – không tìm thấy dòng thiết bị, nhập giá tay"}.`
          : "Không nhận ra nhà cung cấp – nhập tên và đơn giá tay (hoặc thêm từ khoá ở thẻ Nhà cung cấp).",
      );
    },
  });
  const add = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("vendor", vendor.trim());
      if (supplier) fd.append("supplier_id", supplier.id);
      if (mode === "stock") {
        fd.append("out_of_stock", "true");
        fd.append("file", file!);
      } else {
        fd.append("unit_price", price);
        if (read) fd.append("file_id", read.file_id);
        else fd.append("file", file!);
      }
      return api<Purchase>(`/api/workload/purchases/${p.id}/quotes`, { method: "POST", body: fd });
    },
    onSuccess: (np) => {
      onChange(np);
      reset();
      toast.success(mode === "stock" ? "Đã ghi nhận nhà cung cấp không có hàng." : "Đã thêm báo giá.");
    },
  });
  const [confirmDel, setConfirmDel] = useState<{ id: string; vendor: string } | null>(null);
  const remove = useMutation({
    mutationFn: (id: string) => api<Purchase>(`/api/workload/purchases/${p.id}/quotes/${id}`, { method: "DELETE" }),
    onSuccess: (np) => { setConfirmDel(null); onChange(np); },
  });
  const bad = { vendor: vendor.trim().length < 2, price: mode === "quote" && !price, file: !file && !read };
  const rule = p.quote_rule;
  const fileUrl = (id: string) => `/api/workload/purchases/${p.id}/quotes/${id}`;

  return (
    <section aria-label="Báo giá của nhà cung cấp" className="space-y-2">
      <h3 className="flex items-center gap-1 text-sm font-semibold">
        Báo giá của nhà cung cấp ({p.quotes.length})
        <HelpTip title="Quy trình báo giá PC/Laptop">
          <span className="block">PC/Laptop được định giá theo báo giá cho từng đề xuất; hệ thống lấy <b>giá thấp nhất</b> làm đơn giá.</span>
          <span className="block">Giá thấp nhất trên {formatVnd(rule?.threshold ?? 20_000_000)} cần đủ <b>{rule?.count ?? 3} báo giá</b>. Nhà cung cấp không có hàng được tính nếu có <b>ảnh chụp website</b> lúc hỏi hàng; vẫn cần ít nhất 1 báo giá có giá.</span>
          <span className="block">Chọn file PDF báo giá: hệ thống tự nhận ra nhà cung cấp và đọc đơn giá (kiểm tra lại trước khi thêm).</span>
        </HelpTip>
      </h3>
      {p.quotes.length > 0 && (
        <ul className="divide-y divide-line rounded-sm border border-line text-sm">
          {p.quotes.map((q) => (
            <li key={q.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
              <span className="min-w-32 flex-1 font-medium">{q.vendor}</span>
              {q.out_of_stock ? (
                <span className="inline-flex items-center gap-1 rounded-sm bg-skipped-soft px-1.5 py-0.5 text-xs text-skipped">
                  <PackageX className="size-3.5" aria-hidden="true" />Không có hàng
                </span>
              ) : (
                <span className="tabular">{formatVnd(q.unit_price)}</span>
              )}
              {q.lowest && (
                <span className="inline-flex items-center gap-1 rounded-sm bg-done-soft px-1.5 py-0.5 text-xs text-done">
                  <BadgeCheck className="size-3.5" aria-hidden="true" />Thấp nhất – được chọn
                </span>
              )}
              <a className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline" href={fileUrl(q.id)} target="_blank" rel="noreferrer">
                {q.mime.startsWith("image/") ? <Image className="size-4" aria-hidden="true" /> : <FileText className="size-4" aria-hidden="true" />}
                <span className="max-w-48 truncate">{q.filename}</span>
              </a>
              {editable && (
                <Button size="icon" variant="ghost" aria-label={`Xoá ${q.out_of_stock ? "xác nhận không có hàng" : "báo giá"} ${q.vendor}`} loading={remove.isPending && remove.variables === q.id} onClick={() => setConfirmDel({ id: q.id, vendor: q.vendor })}>
                  <Trash2 className="size-4" aria-hidden="true" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {rule?.problem && <p className="text-sm text-waiting">{rule.problem}</p>}
      <ConfirmDialog open={!!confirmDel} onClose={() => setConfirmDel(null)} onConfirm={() => confirmDel && remove.mutate(confirmDel.id)} title="Xoá báo giá?" confirmLabel="Xoá">
        Xoá báo giá của {confirmDel?.vendor}? Không hoàn tác được.
      </ConfirmDialog>

      {editable && (
        <fieldset
          className="space-y-2 rounded-sm border border-line p-3"
          onPaste={(e) => {
            const f = mode === "stock" && pastedImage(e);
            if (f) setFile(f);
          }}
        >
          <legend className="px-1 text-sm font-medium">Thêm báo giá hoặc xác nhận không có hàng</legend>
          <div role="radiogroup" aria-label="Loại" className="flex flex-wrap gap-4 text-sm">
            {(
              [
                ["quote", "Có báo giá (file PDF)"],
                ["stock", "Nhà cung cấp không có hàng"],
              ] as const
            ).map(([k, label]) => (
              <label key={k} className="inline-flex min-h-8 cursor-pointer items-center gap-2">
                <input type="radio" name={`quote-mode-${p.id}`} className="size-4 accent-primary" checked={mode === k} onChange={() => { setMode(k); reset(); }} />
                {label}
              </label>
            ))}
          </div>
          <div className="grid gap-2 sm:grid-cols-[auto_1fr_10rem] sm:items-start">
            <Field
              label={mode === "quote" ? "File báo giá (PDF)" : "Ảnh chụp website"}
              error={mode === "quote" ? "Chọn file PDF." : "Chọn hoặc dán (Ctrl+V) ảnh chụp."}
              showError={tried && bad.file}
            >
              <label className="inline-flex h-9 max-w-56 cursor-pointer items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 text-sm font-medium hover:bg-surface-2 focus-within:outline-2 focus-within:outline-primary">
                <Paperclip className="size-4 shrink-0" aria-hidden="true" />
                <span className="truncate">{readPdf.isPending ? "Đang đọc…" : (file?.name ?? read?.filename ?? (mode === "quote" ? "Chọn PDF" : "Chọn ảnh"))}</span>
                <input
                  type="file"
                  accept={mode === "quote" ? "application/pdf" : "image/png,image/jpeg,application/pdf"}
                  className="sr-only"
                  onChange={(e) => {
                    const f = e.target.files?.[0] ?? null;
                    e.target.value = "";
                    setFile(f);
                    setRead(null);
                    if (f && mode === "quote") readPdf.mutate(f);
                  }}
                />
              </label>
            </Field>
            <Field label="Nhà cung cấp" error="Nhập tên nhà cung cấp." showError={tried && bad.vendor}>
              <Input value={vendor} maxLength={200} list={`suppliers-${p.id}`} onChange={(e) => setVendor(e.target.value)} />
            </Field>
            {mode === "quote" ? (
              <Field label="Đơn giá (đ)" error="Nhập đơn giá." showError={tried && bad.price}>
                <Input inputMode="numeric" value={group(price)} onChange={(e) => setPrice(digits(e.target.value))} />
              </Field>
            ) : (
              <div className="pt-6 text-sm">
                {supplier?.website && (
                  <a className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline" href={supplier.website} target="_blank" rel="noreferrer">
                    <ExternalLink className="size-4" aria-hidden="true" />Mở website
                  </a>
                )}
              </div>
            )}
          </div>
          <datalist id={`suppliers-${p.id}`}>{(suppliers.data ?? []).filter((s) => s.active).map((s) => <option key={s.id} value={s.name} />)}</datalist>
          {read?.line && <p className="font-mono text-xs text-muted">Dòng đọc được: {read.line}</p>}
          <Button size="sm" loading={add.isPending} disabled={readPdf.isPending} onClick={() => { setTried(true); if (!bad.vendor && !bad.price && !bad.file) add.mutate(); }}>
            {mode === "quote" ? "Thêm báo giá" : "Ghi nhận không có hàng"}
          </Button>
        </fieldset>
      )}
    </section>
  );
}
