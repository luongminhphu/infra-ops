import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { BadgeCheck, FileText, Paperclip, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { formatVnd } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { Field, Input } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import type { Purchase } from "../api";

const digits = (s: string) => s.replace(/\D/g, "").slice(0, 11);
const group = (s: string) => (s ? new Intl.NumberFormat("vi-VN").format(Number(s)) : "");

/** Vendor quotes of one proposal (PC/Laptop): add with the PDF, the lowest one sets the unit price. */
export function QuotesBlock({ p, onChange }: { p: Purchase; onChange: (p: Purchase) => void }) {
  const [vendor, setVendor] = useState("");
  const [price, setPrice] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [tried, setTried] = useState(false);
  const editable = p.status === "proposed";
  const add = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("vendor", vendor.trim());
      fd.append("unit_price", price);
      fd.append("file", file!);
      return api<Purchase>(`/api/workload/purchases/${p.id}/quotes`, { method: "POST", body: fd });
    },
    onSuccess: (np) => {
      onChange(np);
      setVendor("");
      setPrice("");
      setFile(null);
      setTried(false);
      toast.success("Đã thêm báo giá.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: (id: string) => api<Purchase>(`/api/workload/purchases/${p.id}/quotes/${id}`, { method: "DELETE" }),
    onSuccess: onChange,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const bad = { vendor: vendor.trim().length < 2, price: !price, file: !file };
  const rule = p.quote_rule;
  return (
    <section aria-label="Báo giá của đối tác" className="space-y-2">
      <h3 className="flex items-center gap-1 text-sm font-semibold">
        Báo giá của đối tác ({p.quotes.length})
        <HelpTip title="Quy trình báo giá PC/Laptop">
          <span className="block">PC/Laptop được định giá theo báo giá cho từng đề xuất. Hệ thống tự lấy <b>giá thấp nhất</b> làm đơn giá.</span>
          <span className="block">Giá thấp nhất trên {formatVnd(rule?.threshold ?? 20_000_000)} thì cần đủ <b>{rule?.count ?? 3} báo giá</b> của các đối tác khác nhau mới duyệt được.</span>
          <span className="block">Mỗi báo giá kèm file PDF của đối tác; file được đính kèm email khi chốt PO.</span>
        </HelpTip>
      </h3>
      {p.quotes.length > 0 && (
        <ul className="divide-y divide-line rounded-sm border border-line text-sm">
          {p.quotes.map((q) => (
            <li key={q.id} className="flex flex-wrap items-center gap-x-3 gap-y-1 px-3 py-2">
              <span className="min-w-32 flex-1 font-medium">{q.vendor}</span>
              <span className="tabular">{formatVnd(q.unit_price)}</span>
              {q.lowest && (
                <span className="inline-flex items-center gap-1 rounded-sm bg-done-soft px-1.5 py-0.5 text-xs text-done">
                  <BadgeCheck className="size-3.5" aria-hidden="true" />Thấp nhất – được chọn
                </span>
              )}
              <a className="inline-flex items-center gap-1 text-primary underline-offset-2 hover:underline" href={`/api/workload/purchases/${p.id}/quotes/${q.id}`} target="_blank" rel="noreferrer">
                <FileText className="size-4" aria-hidden="true" />{q.filename}
              </a>
              {editable && (
                <Button size="icon" variant="ghost" aria-label={`Xoá báo giá ${q.vendor}`} loading={remove.isPending && remove.variables === q.id} onClick={() => remove.mutate(q.id)}>
                  <Trash2 className="size-4" aria-hidden="true" />
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}
      {rule?.problem && <p className="text-sm text-waiting">{rule.problem}</p>}
      {editable && (
        <fieldset className="grid gap-2 rounded-sm border border-line p-3 sm:grid-cols-[1fr_10rem_auto] sm:items-start">
          <legend className="px-1 text-sm font-medium">Thêm báo giá</legend>
          <Field label="Đối tác" error="Nhập tên đối tác." showError={tried && bad.vendor}>
            <Input value={vendor} maxLength={200} onChange={(e) => setVendor(e.target.value)} />
          </Field>
          <Field label="Đơn giá (đ)" error="Nhập đơn giá." showError={tried && bad.price}>
            <Input inputMode="numeric" value={group(price)} onChange={(e) => setPrice(digits(e.target.value))} />
          </Field>
          <Field label="File báo giá (PDF)" error="Chọn file PDF." showError={tried && bad.file}>
            <label className="inline-flex h-9 max-w-56 cursor-pointer items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 text-sm font-medium hover:bg-surface-2 focus-within:outline-2 focus-within:outline-primary">
              <Paperclip className="size-4 shrink-0" aria-hidden="true" /><span className="truncate">{file?.name ?? "Chọn PDF"}</span>
              <input type="file" accept="application/pdf" className="sr-only" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </label>
          </Field>
          <div className="sm:col-span-3">
            <Button size="sm" loading={add.isPending} onClick={() => { setTried(true); if (!bad.vendor && !bad.price && !bad.file) add.mutate(); }}>
              Thêm báo giá
            </Button>
          </div>
        </fieldset>
      )}
    </section>
  );
}
