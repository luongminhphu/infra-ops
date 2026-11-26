import { useQuery } from "@tanstack/react-query";
import { api } from "@/core/api/client";
import type { Status } from "@/core/ui/status";

export type JobStatus = "draft" | "scheduled" | "running" | "waiting" | "failed" | "completed" | "cancelled";
export type StepStatus = "pending" | "in_progress" | "waiting" | "verify" | "done" | "failed" | "skipped";

export interface JobAsset {
  id: string;
  name: string;
  tag?: string | null;
  product_type?: string | null;
}

export interface JobStep {
  key: string;
  title: string;
  kind: "auto" | "manual";
  status: StepStatus;
  seq: number;
  description: string;
  writes: string[];
  skippable: boolean;
  requires_confirm: boolean;
  confirmed_by: string | null;
  confirmed_at: string | null;
  confirm_text: string;
  attempts: number;
  next_run_at: string | null;
  waiting_since: string | null;
  started_at: string | null;
  finished_at: string | null;
  actor: string | null;
  message: string;
  before_state: Record<string, unknown> | null;
  output: Record<string, unknown>;
  checklist: { id: string; text: string; checked: boolean }[] | null;
  evidence_required: boolean;
  verified_by: string | null;
  verified_at: string | null;
  verify_note: string | null;
  evidence: Evidence[];
}

export interface Assignment {
  from_name: string | null;
  to_name: string;
  step_key: string | null;
  source: "sdp" | "app";
  actor: string;
  reason: string;
  at: string;
}

/** Onboard / offboard / transfer (the reassignment workflow is an internal helper). */
export const JOB_WORKFLOWS = "lifecycle.onboard,lifecycle.offboard,lifecycle.transfer";

export interface Evidence {
  id: string;
  step_key: string;
  kind: "image" | "file" | "link" | "note";
  filename: string | null;
  url: string | null;
  text: string | null;
  created_by: string;
  created_at: string;
}

export interface Job {
  id: string;
  workflow_key: "lifecycle.onboard" | "lifecycle.offboard" | "lifecycle.transfer" | string;
  workflow_title: string;
  status: JobStatus;
  subject_key: string;
  subject_label: string;
  external_ref: string | null;
  params: {
    kind: "onboard" | "offboard" | "transfer";
    request_id: string;
    request_display_id: string | null;
    effective_date: string | null;
    employee: { id: string; full_name: string; department: string; job_title: string; work_location: string };
    assets?: JobAsset[];
    sdp_user_id: string | null;
    sdp_user_name: string | null;
  };
  scheduled_for: string | null;
  current_step: string | null;
  created_by: string;
  created_at: string;
  updated_at: string;
  started_by: string | null;
  started_at: string | null;
  closed_at: string | null;
  close_reason: string | null;
  version: number;
  progress: { done: number; total: number };
  /** Running but not advanced by the worker for this many minutes (worker down / step stuck). */
  stalled_minutes: number | null;
  /** Checklist version frozen at start; null = no step verification. */
  checklist_version: number | null;
  assignee: { user_id: string | null; name: string } | null;
  assignments: Assignment[];
  steps: JobStep[];
  events?: { at: string; step_key: string | null; level: "info" | "warn" | "error"; message: string; actor: string | null }[];
}

export const JOB_STATUS: Record<JobStatus, { label: string; status: Status }> = {
  draft: { label: "Nháp", status: "pending" },
  scheduled: { label: "Đã lên lịch", status: "waiting" },
  running: { label: "Đang chạy", status: "in_progress" },
  waiting: { label: "Chờ xử lý", status: "waiting" },
  failed: { label: "Lỗi", status: "failed" },
  completed: { label: "Hoàn tất", status: "done" },
  cancelled: { label: "Đã huỷ", status: "disabled" },
};

export const STEP_STATUS: Record<StepStatus, { label: string; status: Status }> = {
  pending: { label: "Chưa chạy", status: "pending" },
  in_progress: { label: "Đang chạy", status: "in_progress" },
  waiting: { label: "Đang chờ", status: "waiting" },
  verify: { label: "Chờ Operator kiểm tra", status: "waiting" },
  done: { label: "Hoàn tất", status: "done" },
  failed: { label: "Lỗi", status: "failed" },
  skipped: { label: "Bỏ qua", status: "skipped" },
};

/** Job state for people: a waiting job says what it waits for and at which step. */
export function idleText(minutes: number): string {
  return minutes >= 60 * 24 ? `${Math.floor(minutes / 1440)} ngày` : minutes >= 60 ? `${Math.floor(minutes / 60)} giờ` : `${minutes} phút`;
}

export function jobStatusLabel(job: Pick<Job, "status" | "current_step" | "steps"> & { stalled_minutes?: number | null }): string {
  if (job.stalled_minutes) {
    const cur = job.steps.find((x) => x.key === job.current_step);
    return `Đứng yên ${idleText(job.stalled_minutes)}${cur ? ` ở bước ${cur.seq}` : ""}`;
  }
  const s = job.status === "waiting" ? job.steps.find((x) => x.key === job.current_step) : undefined;
  if (!s) return JOB_STATUS[job.status].label;
  const at = `bước ${s.seq}`;
  if (s.status === "verify") return `Chờ Operator kiểm tra ${at}`;
  if (s.requires_confirm && !s.confirmed_at && s.status === "waiting") return `Chờ nhập lại Mã NV ở ${at}`;
  if (s.kind === "manual") return `Chờ Operator thực hiện ${at}`;
  return `Chờ hệ thống ở ${at}`;
}

/** What the operator has to do now on a running job – an instruction, or that nothing is needed. */
export function operatorTask(job: Pick<Job, "status" | "current_step" | "steps"> & { stalled_minutes?: number | null }): { act: boolean; text: string } {
  const s = job.steps.find((x) => x.key === job.current_step);
  const at = s ? `bước ${s.seq} “${s.title}”` : "bước hiện tại";
  if (job.status === "failed") return { act: true, text: `Bạn cần: đọc lỗi ở ${at}, sửa nguyên nhân rồi bấm Chạy lại bước (không rõ thì báo Supervisor).` };
  if (job.stalled_minutes) return { act: false, text: `Không cần làm gì ở ${at}: hệ thống chưa chạy được bước tự động – báo Quản trị kiểm tra dịch vụ chạy nền, Job tự chạy tiếp sau đó.` };
  if (job.status === "waiting" && s) {
    if (s.status === "verify") return { act: true, text: `Bạn cần: kiểm tra kết quả ${at} theo danh sách bên dưới rồi xác nhận.` };
    if (s.requires_confirm && !s.confirmed_at) return { act: true, text: `Bạn cần: nhập lại Mã NV để hệ thống chạy ${at}.` };
    if (s.kind === "manual") return { act: true, text: `Bạn cần: thực hiện ${at} rồi xác nhận hoàn tất.` };
    return { act: false, text: `Không cần làm gì: hệ thống đang chờ để tự kiểm tra lại ${at}.` };
  }
  return { act: false, text: `Không cần làm gì: hệ thống đang chạy ${at}, trang tự cập nhật.` };
}

export const WORKFLOW_LABEL: Record<string, string> = { "lifecycle.onboard": "Onboard", "lifecycle.offboard": "Offboard", "lifecycle.transfer": "Thuyên chuyển" };
/** Request kinds that get a Job, with the label used on buttons and headings. */
export const JOB_KIND_LABEL: Record<string, string> = { onboard: "onboard", offboard: "offboard", transfer: "thuyên chuyển" };
export const SYSTEM_WRITE_LABEL: Record<string, string> = { sdp: "ServiceDesk Plus", biostar: "BioStar" };

export function useJob(id: string) {
  return useQuery({
    queryKey: ["job", id],
    queryFn: () => api<Job>(`/api/core/jobs/${id}`),
    refetchInterval: (q) => (q.state.data && ["running", "waiting", "scheduled"].includes(q.state.data.status) ? 4000 : false),
  });
}

export interface WorkflowInfo {
  key: string;
  module_id: string;
  title: string;
  verify_steps: boolean;
  checklist_version: number;
  required_for_close: string[];
  steps: {
    key: string;
    title: string;
    kind: "auto" | "manual";
    description: string;
    writes: string[];
    confirm: boolean;
    skippable: boolean;
    waits: boolean;
    checklist: { items: { id: string; text: string }[]; evidence_required: boolean };
  }[];
  history: { version: number; note: string; created_by: string; created_at: string }[];
}
