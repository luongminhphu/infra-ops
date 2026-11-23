import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input, Select, Textarea } from "@/core/ui/field";
import type { DeviceNeed, Purchase, Ticket } from "./api";
import { KIND_LABEL } from "./labels";

/** Propose a device purchase, from a suggested SDP request or on its own. Stays in infra-ops. */
export function PurchaseDialog({ ticket, onClose }: { ticket?: Ticket; onClose: () => void }) {
  const qc = useQueryClient();
  const [kind, setKind] = useState<DeviceNeed | "new">(ticket?.device_need ?? "replace");
  const [item, setItem] = useState("");
  const [quantity, setQuantity] = useState(1);
  const [reason, setReason] = useState(ticket ? `Request #${ticket.display_id ?? ticket.id}: ${ticket.subject}` : "");
  const [tried, setTried] = useState(false);
  const save = useMutation({
    mutationFn: () => api<Purchase>("/api/workload/purchases", { method: "POST", json: { ticket_id: ticket?.id ?? null, kind, item, quantity, reason } }),
    onSuccess: (p) => {
      toast.success(`Đã lập đề xuất mua “${p.item}”. Manager sẽ duyệt ở trang Đề xuất mua thiết bị.`);
      void qc.invalidateQueries({ queryKey: ["workload"] });
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const itemBad = item.trim().length < 2;
  return (
    <Dialog
      open
      onClose={onClose}
      title="Lập đề xuất mua thiết bị"
      description={ticket ? `Từ request #${ticket.display_id ?? ticket.id} – ${ticket.requester_name ?? ""}` : "Đề xuất không gắn với request SDP."}
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
      <div className="grid gap-3 sm:grid-cols-[1fr_8rem]">
        <Field label="Thiết bị / linh kiện" required error="Nhập tên thiết bị." showError={tried && itemBad} className="sm:col-span-2">
          <Input data-autofocus value={item} maxLength={300} onChange={(e) => setItem(e.target.value)} placeholder="Ví dụ: Laptop Dell Latitude 5440, RAM 16GB DDR5" />
        </Field>
        <Field label="Loại">
          <Select value={kind} onChange={(e) => setKind(e.target.value as DeviceNeed | "new")}>
            {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        <Field label="Số lượng">
          <Input type="number" min={1} max={500} value={quantity} onChange={(e) => setQuantity(Math.max(1, Number(e.target.value) || 1))} />
        </Field>
        <Field label="Lý do" className="sm:col-span-2">
          <Textarea rows={3} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} />
        </Field>
      </div>
    </Dialog>
  );
}
