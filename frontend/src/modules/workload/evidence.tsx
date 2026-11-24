import type { ClipboardEvent } from "react";
import { api } from "@/core/api/client";
import { HelpTip } from "@/core/ui/help-tip";
import type { Purchase } from "./api";

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

