import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { PackageCheck } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import type { Purchase } from "../api";

/** Goods arrived: serials become In Store assets on SDP (worker job), or just "received" for parts. */
export function ReceiveDialog({ p, onClose, onDone }: { p: Purchase; onClose: () => void; onDone: () => void }) {
  const tracked = !!p.sdp_product;
  const [units, setUnits] = useState(() => Array.from({ length: tracked ? p.quantity : 0 }, () => ({ serial: "", asset_tag: "" })));
  const [note, setNote] = useState("");
  const [tried, setTried] = useState(false);
  const serials = units.map((u) => u.serial.trim().toLowerCase());
  const bad = (i: number) => serials[i]!.length < 2 || serials.indexOf(serials[i]!) !== i;
  const anyBad = units.some((_, i) => bad(i));
  const save = useMutation({
    mutationFn: () => api<Purchase>(`/api/workload/purchases/${p.id}/receive`, { method: "POST", json: { units, note } }),
    onSuccess: (np) => {
      toast.success(
        np.status === "received"
          ? `Đã nhận hàng “${np.catalog_item?.name ?? np.item}”.`
          : `Đang tạo ${p.quantity} tài sản In Store trên ServiceDesk Plus – trạng thái cập nhật khi worker chạy xong.`,
      );
      onDone();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const set = (i: number, patch: Partial<(typeof units)[number]>) => setUnits((cur) => cur.map((u, j) => (j === i ? { ...u, ...patch } : u)));
  return (
    <Dialog
      open
      onClose={onClose}
      size={tracked ? "lg" : "sm"}
      title={`Nhận hàng “${p.catalog_item?.name ?? p.item}” × ${p.quantity}`}
      description={
        tracked
          ? `Mỗi thiết bị thành một tài sản “${p.sdp_product}” ở trạng thái In Store trên ServiceDesk Plus, sẵn sàng cấp phát.`
          : "Thiết bị này không quản lý tài sản trên SDP (bảng giá không ghi sản phẩm SDP) – chỉ đánh dấu đã nhận hàng."
      }
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" loading={save.isPending} onClick={() => { setTried(true); if (!anyBad) save.mutate(); }}>
            <PackageCheck className="size-4" aria-hidden="true" />{tracked ? "Nhập kho SDP" : "Đã nhận hàng"}
          </Button>
        </>
      }
    >
      {tracked && (
        <fieldset>
          <legend className="mb-2 flex items-center gap-1 text-sm font-medium">
            Serial và asset tag từng thiết bị
            <HelpTip title="Nhập kho trên SDP">
              <span className="block">Serial bắt buộc, không trùng; asset tag (nếu có) dùng làm tên tài sản.</span>
              <span className="block">Worker tìm serial trên SDP trước: serial đã có thì giữ nguyên, không tạo trùng – bấm lại an toàn khi lỗi giữa chừng.</span>
              <span className="block">Xong, trang <b>Kho & cấp phát</b> được đồng bộ lại để gợi ý thiết bị mới cho nhân viên sắp onboard.</span>
            </HelpTip>
          </legend>
          <ol className="space-y-2">
            {units.map((u, i) => (
              <li key={i} className="grid items-start gap-2 sm:grid-cols-[2rem_1fr_1fr]">
                <span className="pt-2 text-sm text-muted tabular" aria-hidden="true">{i + 1}.</span>
                <Field label={`Serial thiết bị ${i + 1}`} required error={serials[i] && serials.indexOf(serials[i]!) !== i ? "Serial bị trùng." : "Nhập serial."} showError={tried && bad(i)}>
                  <Input data-autofocus={i === 0} value={u.serial} maxLength={100} onChange={(e) => set(i, { serial: e.target.value })} />
                </Field>
                <Field label={`Asset tag ${i + 1}`}>
                  <Input value={u.asset_tag} maxLength={100} onChange={(e) => set(i, { asset_tag: e.target.value })} />
                </Field>
              </li>
            ))}
          </ol>
        </fieldset>
      )}
      {!tracked && (
        <Field label="Ghi chú">
          <Textarea data-autofocus rows={2} maxLength={1000} value={note} onChange={(e) => setNote(e.target.value)} />
        </Field>
      )}
    </Dialog>
  );
}
