import type { Status } from "@/core/ui/status";
import type { CellStatus, Kind, Severity, SyncRun } from "./api";

export const KIND_LABEL: Record<Kind, string> = {
  onboard: "Nhân viên mới",
  probation_to_official: "Thử việc → chính thức",
  transfer: "Thuyên chuyển",
  offboard: "Nghỉ việc",
  ignore: "Bỏ qua",
};

export const SEVERITY: Record<Severity, { label: string; short: string; status: Status }> = {
  high: { label: "Mức cao", short: "Cao", status: "failed" },
  medium: { label: "Trung bình", short: "Trung bình", status: "mismatch" },
  low: { label: "Thấp", short: "Thấp", status: "warning" },
};

export const SYSTEM_LABEL: Record<string, string> = { sdp: "SDP", assets: "Tài sản", biostar: "BioStar" };

export const CELL_STATUS: Record<CellStatus, Status> = {
  match: "match",
  mismatch: "mismatch",
  pending: "pending",
  skipped: "skipped",
  warning: "warning",
  failed: "failed",
};

export const RUN_STATUS: Record<SyncRun["status"], { label: string; status: Status }> = {
  running: { label: "Đang chạy", status: "in_progress" },
  success: { label: "Thành công", status: "done" },
  partial: { label: "Một phần", status: "warning" },
  failure: { label: "Thất bại", status: "failed" },
};

export const SOURCE_LABEL: Record<string, string> = {
  sdp: "ServiceDesk Plus",
  requests: "Request RTR",
  sdp_users: "User SDP",
  sdp_assets: "Tài sản SDP",
  biostar: "BioStar",
};

export const NOTE_TITLE: Record<string, string> = {
  request_parse_error: "Request thiếu Mã NV",
  asset_state_mismatch: "Trạng thái tài sản",
  sdp_no_employee_ids: "User SDP chưa có Mã NV",
};
