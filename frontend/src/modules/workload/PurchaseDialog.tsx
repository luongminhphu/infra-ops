import { useState, type ClipboardEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Paperclip, X } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { formatVnd } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input, Select, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { useCatalog, type DeviceNeed, type Purchase, type Ticket } from "./api";
import { KIND_LABEL } from "./labels";

export interface PurchasePrefill {
  kind?: DeviceNeed | "new";
  item?: string;
  catalogName?: string;
  quantity?: number;
  source?: "onboard" | "manual";
  sourceRef?: string;
  employee?: string;
  reason?: string;
}

export const EVIDENCE_ACCEPT = "image/png,image/jpeg,application/pdf";

export async function uploadEvidence(purchaseId: string, file: File): Promise<Purchase> {
  const fd = new FormData();
  fd.append("file", file);
  return api<Purchase>(`/api/workload/purchases/${purchaseId}/evidence`, { method: "POST", body: fd });
}

export function pastedImage(e: ClipboardEvent): File | null {
  const file = Array.from(e.clipboardData.files).find((f) => f.type.startsWith("image/"));
  if (!file) return null;
  e.preventDefault();
  return new File([file], `anh-chup-${Date.now()}.png`, { type: file.type });
}

export function EvidenceHelp() {
  return (
    <HelpTip title="Bằng chứng Function Head duyệt">
      <span className="block">Đính kèm ảnh chụp email / tin nhắn / phiếu mà <b>Function Head của người dùng</b> đã đồng ý cấp thiết bị.</span>
      <span className="block">Nhận PNG, JPG, PDF (tối đa 10 file). Có thể dán ảnh chụp màn hình bằng <b>Ctrl+V</b>.</span>
      <span className="block">Supervisor chỉ duyệt được đề xuất đã có bằng chứng.</span>
    </HelpTip>
  );
}

/** Propose a device purchase (from a helpdesk request, an onboard allocation check, or on its own). */
export function PurchaseDialog({ ticket, prefill, onClose }: { ticket?: Ticket; prefill?: PurchasePrefill; onClose: () => void }) {
  const qc = useQueryClient();
  const catalog = useCatalog();
  const active = (catalog.data ?? []).filter((c) => c.active);
  const initialCat = prefill?.catalogName ? active.find((c) => c.name.toLowerCase() === prefill.catalogName!.toLowerCase()) : undefined;
  const [catalogId, setCatalogId] = useState<string | null>(null);
  const catId = catalogId ?? initialCat?.id ?? "";
  const cat = active.find((c) => c.id === catId);
  const [kind, setKind] = useState<DeviceNeed | "new">(prefill?.kind ?? ticket?.device_need ?? "replace");
  const [item, setItem] = useState(prefill?.item ?? "");
  const [quantity, setQuantity] = useState(prefill?.quantity ?? 1);
  const [employee, setEmployee] = useState(prefill?.employee ?? ticket?.requester_name ?? "");
  const [reason, setReason] = useState(prefill?.reason ?? (ticket ? `Request #${ticket.display_id ?? ticket.id}: ${ticket.subject}` : ""));
  const [files, setFiles] = useState<File[]>([]);
  const [tried, setTried] = useState(false);
  const itemText = item.trim() || cat?.name || "";
  const save = useMutation({
    mutationFn: async () => {
      let p = await api<Purchase>("/api/workload/purchases", {
        method: "POST",
        json: {
          ticket_id: ticket?.id ?? null,
          source: prefill?.source ?? "manual",
          source_ref: prefill?.sourceRef ?? null,
          employee: employee || null,
          catalog_item_id: cat?.id ?? null,
          kind,
          item: itemText,
          quantity,
          reason,
        },
      });
      for (const f of files) p = await uploadEvidence(p.id, f);
      return p;
    },
    onSuccess: (p) => {
      toast.success(
        p.evidence.length
          ? `Đã lập đề xuất “${p.item}” kèm ${p.evidence.length} bằng chứng. Supervisor sẽ duyệt và gom vào PO.`
          : `Đã lập đề xuất “${p.item}”. Nhớ đính kèm bằng chứng Function Head duyệt trước khi Supervisor duyệt.`,
      );
      void qc.invalidateQueries({ queryKey: ["workload"] });
      onClose();
    },
    onError: (e) => {
      toast.error(errorMessage(e));
      void qc.invalidateQueries({ queryKey: ["workload"] });
    },
  });
  const itemBad = itemText.length < 2;
  const addFiles = (list: FileList | File[] | null) => list && setFiles((cur) => [...cur, ...Array.from(list)].slice(0, 10));
  const source = ticket
    ? `Từ request #${ticket.display_id ?? ticket.id} – ${ticket.requester_name ?? ""}`
    : prefill?.source === "onboard"
      ? `Từ kiểm tra cấp phát onboard ${prefill.sourceRef ? `(request #${prefill.sourceRef})` : ""}`
      : "Đề xuất không gắn với request SDP.";
  return (
    <Dialog
      open
      onClose={onClose}
      size="lg"
      title="Lập đề xuất mua thiết bị"
      description={source}
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button
            variant="primary"
            loading={save.isPending}
            onClick={() => {
              setTried(true);
              if (!itemBad) save.mutate();
            }}
          >
            Lập đề xuất
          </Button>
        </>
      }
    >
      <div className="grid gap-3 sm:grid-cols-[1fr_8rem_8rem]" onPaste={(e) => { const f = pastedImage(e); if (f) addFiles([f]); }}>
        <Field
          label="Thiết bị trong bảng giá"
          hint={cat ? `Đơn giá ${formatVnd(cat.unit_price)}${cat.unit_price ? ` · thành tiền ${formatVnd(cat.unit_price * quantity)}` : ""}` : "Chưa có trong bảng giá? Nhập tên bên dưới, Supervisor sẽ chọn khi duyệt."}
          className="sm:col-span-3"
        >
          <Select data-autofocus value={catId} onChange={(e) => setCatalogId(e.target.value)}>
            <option value="">— Chưa chọn —</option>
            {active.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}{c.unit_price ? ` – ${formatVnd(c.unit_price)}` : " – chưa có giá"}
              </option>
            ))}
          </Select>
        </Field>
        <Field label="Mô tả thiết bị / linh kiện" required={!cat} error="Nhập tên thiết bị hoặc chọn trong bảng giá." showError={tried && itemBad} className="sm:col-span-3">
          <Input value={item} maxLength={300} onChange={(e) => setItem(e.target.value)} placeholder={cat?.name ?? "Ví dụ: Laptop Dell Latitude 5440, RAM 16GB DDR5"} />
        </Field>
        <Field label="Người dùng thiết bị">
          <Input value={employee} maxLength={256} onChange={(e) => setEmployee(e.target.value)} />
        </Field>
        <Field label="Loại">
          <Select value={kind} onChange={(e) => setKind(e.target.value as DeviceNeed | "new")}>
            {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <Field label="Số lượng">
          <Input type="number" min={1} max={500} value={quantity} onChange={(e) => setQuantity(Math.max(1, Number(e.target.value) || 1))} />
        </Field>
        <Field label="Lý do" className="sm:col-span-3">
          <Textarea rows={2} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
        <fieldset className="sm:col-span-3">
          <legend className="mb-1 flex items-center gap-1 text-sm font-medium">
            Bằng chứng Function Head đã duyệt <EvidenceHelp />
          </legend>
          <label className="inline-flex h-9 cursor-pointer items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 text-sm font-medium hover:bg-surface-2 focus-within:outline-2 focus-within:outline-primary">
            <Paperclip className="size-4" aria-hidden="true" />Chọn file / ảnh
            <input type="file" multiple accept={EVIDENCE_ACCEPT} className="sr-only" onChange={(e) => { addFiles(e.target.files); e.target.value = ""; }} />
          </label>
          <span className="ml-2 text-xs text-muted">PNG, JPG, PDF · hoặc dán ảnh chụp (Ctrl+V)</span>
          {files.length > 0 && (
            <ul className="mt-2 space-y-1 text-sm">
              {files.map((f, i) => (
                <li key={`${f.name}-${i}`} className="flex items-center gap-2">
                  <Paperclip className="size-3.5 text-muted" aria-hidden="true" />
                  <span className="truncate">{f.name}</span>
                  <Button size="icon" variant="ghost" aria-label={`Bỏ ${f.name}`} onClick={() => setFiles((cur) => cur.filter((_, j) => j !== i))}>
                    <X className="size-4" aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </fieldset>
      </div>
    </Dialog>
  );
}
