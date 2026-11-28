import { useQuery } from "@tanstack/react-query";
import { api } from "@/core/api/client";
import type { Status } from "@/core/ui/status";

export type DocStatus = "draft" | "finalized" | "signed" | "void";
export type ShortKind = "handover" | "return";
export type Check = "ok" | "na" | "";
export type Condition = "good" | "damaged" | "missing_accessory" | "not_returned";

export interface AssetLine {
  name: string;
  tag: string;
  date: string | null;
  note: string;
  sdp_asset_id?: string | null;
  condition?: Condition;
}

export interface DocData {
  employee: { id: string; full_name: string; department: string; job_title: string; work_location: string };
  date: string;
  it_rep: string;
  reviewer: string;
  request_id: string | null;
  assets: AssetLine[];
  checklist: Check[];
  software?: boolean[];
  software_other?: string;
}

export interface Doc {
  id: string;
  kind: string;
  kind_title: string;
  number: string | null;
  status: DocStatus;
  subject_key: string | null;
  subject_label: string;
  external_ref: string | null;
  data: DocData;
  created_by: string;
  created_at: string;
  finalized_at: string | null;
  finalized_by: string | null;
  signed_at: string | null;
  signed_by: string | null;
  voided_at: string | null;
  void_reason: string | null;
  has_pdf: boolean;
  has_signed: boolean;
  can_edit: boolean;
  can_upload_signed: boolean;
  events: { type: string; ok: boolean; request_id?: string; error?: string; by?: string; at: string }[];
  version: number;
}

export interface Meta {
  kinds: Record<ShortKind, string>;
  handover_checklist: string[];
  handover_software: string[];
  return_checklist: string[];
  conditions: Record<Condition, string>;
}

export interface KindInfo {
  key: string;
  title: string;
  prefix: string;
  placeholders: { name: string; description: string; example: string }[];
  active_version: number | null;
  can_create: boolean;
  can_manage: boolean;
}

export const DOC_STATUS: Record<DocStatus, { label: string; status: Status }> = {
  draft: { label: "Nháp", status: "pending" },
  finalized: { label: "Chờ ký", status: "waiting" },
  signed: { label: "Đã ký", status: "done" },
  void: { label: "Đã huỷ", status: "disabled" },
};

export const SHORT_OF: Record<string, ShortKind> = { "lifecycle.handover": "handover", "lifecycle.return": "return" };
export const KIND_SHORT_TITLE: Record<ShortKind, string> = { handover: "Bàn giao", return: "Thu hồi" };

export function useDocMeta() {
  return useQuery({ queryKey: ["lifecycle", "doc-meta"], queryFn: () => api<Meta>("/api/lifecycle/documents/meta"), staleTime: Infinity });
}

export function useKinds() {
  return useQuery({ queryKey: ["document-kinds"], queryFn: () => api<KindInfo[]>("/api/core/document-kinds") });
}
