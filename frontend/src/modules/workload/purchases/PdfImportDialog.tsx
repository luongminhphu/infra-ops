import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FileSearch, Paperclip } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/core/api/client";
import { formatNumber, formatVnd, isoDateVn } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { DateInput } from "@/core/ui/date-input";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import type { CatalogItem } from "../api";

interface Suggestion {
  id: string;
  name: string;
  current_price: number | null;
  price: number;
  line: string;
}
interface ReadResult {
  file_id: string;
  filename: string;
  supplier: { id: string; name: string } | null;
  suggestions: Suggestion[];
}

const digits = (s: string) => s.replace(/\D/g, "").slice(0, 11);
const group = (n: number | string) => (n ? formatNumber(Number(n)) : "");

/** Read a vendor's PDF quote, let the supervisor confirm the matched prices, then update the price list. */
export function PdfImportDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const [file, setFile] = useState<File | null>(null);
  const [vendor, setVendor] = useState("");
  const [quoted, setQuoted] = useState(isoDateVn());
  const [quotedBad, setQuotedBad] = useState(false);
  const [tried, setTried] = useState(false);
  const [applyTried, setApplyTried] = useState(false);
  const [result, setResult] = useState<ReadResult | null>(null);
  const [rows, setRows] = useState<Record<string, { on: boolean; price: string }>>({});
  const read = useMutation({
    mutationFn: () => {
      const fd = new FormData();
      fd.append("file", file!);
      return api<ReadResult>("/api/workload/catalog/quote-pdf", { method: "POST", body: fd });
    },
    onSuccess: (r) => {
      setResult(r);
      if (r.supplier) setVendor(r.supplier.name);
      setRows(Object.fromEntries(r.suggestions.map((s) => [s.id, { on: true, price: String(s.price) }])));
    },
  });
  const chosen = result?.suggestions.filter((s) => rows[s.id]?.on && Number(rows[s.id]!.price) > 0) ?? [];
  const apply = useMutation({
    mutationFn: () =>
      api<CatalogItem[]>("/api/workload/catalog/apply-quote", {
        method: "POST",
        json: { file_id: result!.file_id, vendor, quoted_at: quoted || null, prices: chosen.map((s) => ({ id: s.id, unit_price: Number(rows[s.id]!.price) })) },
      }),
    onSuccess: (r) => {
      toast.success(`Đã cập nhật giá ${r.length} thiết bị theo báo giá ${vendor}. PO nháp đã được tính lại.`);
      void qc.invalidateQueries({ queryKey: ["workload"] });
      onClose();
    },
  });
  const vendorBad = vendor.trim().length < 2;

  return (
    <Dialog
      open
      size="xl"
      onClose={onClose}
      title="Cập nhật giá từ file báo giá PDF"
      description="Hệ thống đọc file PDF của đối tác, tìm các thiết bị có trong Bảng giá và gợi ý đơn giá. Kiểm tra từng dòng trước khi cập nhật."
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          {result ? (
            <Button variant="primary" disabled={chosen.length === 0} loading={apply.isPending} onClick={() => { setApplyTried(true); if (!vendorBad && !quotedBad) apply.mutate(); }}>
              Cập nhật {chosen.length} giá
            </Button>
          ) : (
            <Button variant="primary" loading={read.isPending} onClick={() => { setTried(true); if (file) read.mutate(); }}>
              <FileSearch className="size-4" aria-hidden="true" />Đọc báo giá
            </Button>
          )}
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-[1fr_1fr_10rem]">
        <Field label="File báo giá (PDF)" required error="Chọn file PDF." showError={tried && !file}>
          <label className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 text-sm font-medium hover:bg-surface-2 focus-within:outline-2 focus-within:outline-primary">
            <Paperclip className="size-4 shrink-0" aria-hidden="true" /><span className="truncate">{file?.name ?? "Chọn PDF"}</span>
            <input data-autofocus type="file" accept="application/pdf" className="sr-only" onChange={(e) => { setFile(e.target.files?.[0] ?? null); setResult(null); }} />
          </label>
        </Field>
        <Field label="Đối tác báo giá" required error="Nhập tên đối tác." showError={applyTried && vendorBad}>
          <Input value={vendor} maxLength={200} onChange={(e) => setVendor(e.target.value)} />
        </Field>
        <Field label="Ngày báo giá" error="Ngày theo dạng dd/MM/yyyy." showError={(tried || applyTried) && quotedBad}>
          <DateInput value={quoted} onChange={setQuoted} onInvalidChange={setQuotedBad} />
        </Field>
      </div>

      {result && (
        <section aria-label="Giá đọc được" className="mt-4">
          <h3 className="mb-2 flex items-center gap-1 text-sm font-semibold">
            Giá đọc được từ {result.filename} ({result.suggestions.length})
            <span className="font-normal text-muted">
              · {result.supplier ? `nhận diện: ${result.supplier.name}` : "không nhận ra nhà cung cấp (đọc theo cách mặc định)"}
            </span>
            <HelpTip title="Kiểm tra giá gợi ý">
              <span className="block">Mỗi dòng là một thiết bị trong Bảng giá tìm thấy trong PDF; giá gợi ý là số tiền nhỏ nhất trên dòng đó (đơn giá, không phải thành tiền).</span>
              <span className="block">Đối chiếu với “Dòng trong PDF”, sửa giá nếu sai, bỏ chọn dòng không đúng. File PDF được lưu kèm từng thiết bị.</span>
              <span className="block">PDF bản scan (ảnh) không đọc được – nhập giá tay bằng nút Sửa.</span>
            </HelpTip>
          </h3>
          {result.suggestions.length === 0 ? (
            <p className="text-sm text-waiting">Không tìm thấy thiết bị nào của Bảng giá trong file này – kiểm tra tên thiết bị trong Bảng giá có giống trong báo giá không, hoặc nhập giá tay.</p>
          ) : (
            <div className="overflow-x-auto rounded-sm border border-line" tabIndex={0} role="region" aria-label="Bảng giá gợi ý">
              <table className="w-full text-sm">
                <caption className="sr-only">Giá gợi ý từ file PDF</caption>
                <thead>
                  <tr className="border-b border-line text-left text-xs text-muted">
                    <th scope="col" className="px-3 py-2 font-medium">Cập nhật</th>
                    <th scope="col" className="px-3 py-2 font-medium">Thiết bị</th>
                    <th scope="col" className="px-3 py-2 text-right font-medium">Giá hiện tại</th>
                    <th scope="col" className="px-3 py-2 font-medium">Giá mới (đ)</th>
                    <th scope="col" className="px-3 py-2 font-medium">Dòng trong PDF</th>
                  </tr>
                </thead>
                <tbody>
                  {result.suggestions.map((s) => {
                    const r = rows[s.id]!;
                    const set = (patch: Partial<typeof r>) => setRows((cur) => ({ ...cur, [s.id]: { ...cur[s.id]!, ...patch } }));
                    return (
                      <tr key={s.id} className="border-b border-line last:border-0">
                        <td className="px-3 py-2">
                          <input type="checkbox" className="size-4 accent-primary" checked={r.on} aria-label={`Cập nhật giá ${s.name}`} onChange={(e) => set({ on: e.target.checked })} />
                        </td>
                        <td className="px-3 py-2 font-medium">{s.name}</td>
                        <td className="px-3 py-2 text-right tabular whitespace-nowrap">{formatVnd(s.current_price)}</td>
                        <td className="px-3 py-2">
                          <Input className="w-36" inputMode="numeric" aria-label={`Giá mới ${s.name}`} value={group(r.price)} onChange={(e) => set({ price: digits(e.target.value) })} />
                        </td>
                        <td className="max-w-80 px-3 py-2 font-mono text-xs text-muted">{s.line}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      )}
    </Dialog>
  );
}
