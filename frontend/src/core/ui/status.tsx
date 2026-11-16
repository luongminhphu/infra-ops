import type { LucideIcon } from "lucide-react";
import {
  CircleCheck,
  CircleDashed,
  CircleMinus,
  CircleX,
  Clock,
  LoaderCircle,
  PowerOff,
  TriangleAlert,
} from "lucide-react";
import { cn } from "@/core/lib/cn";

/**
 * Unified status vocabulary. Every status is icon + label + colour, never colour alone.
 * Workflow states (Phase 4+) and reconciliation states share the same palette.
 */
export type Status =
  | "done"
  | "in_progress"
  | "waiting"
  | "failed"
  | "skipped"
  | "pending"
  | "match"
  | "mismatch"
  | "warning"
  | "disabled";

interface StatusDef {
  label: string;
  icon: LucideIcon;
  fg: string;
  bg: string;
  spin?: boolean;
}

export const STATUS: Record<Status, StatusDef> = {
  done: { label: "Hoàn tất", icon: CircleCheck, fg: "text-done", bg: "bg-done-soft" },
  in_progress: { label: "Đang chạy", icon: LoaderCircle, fg: "text-progress", bg: "bg-progress-soft", spin: true },
  waiting: { label: "Đang chờ", icon: Clock, fg: "text-waiting", bg: "bg-waiting-soft" },
  failed: { label: "Lỗi", icon: CircleX, fg: "text-failed", bg: "bg-failed-soft" },
  skipped: { label: "Bỏ qua", icon: CircleMinus, fg: "text-skipped", bg: "bg-skipped-soft" },
  pending: { label: "Chưa làm", icon: CircleDashed, fg: "text-skipped", bg: "bg-skipped-soft" },
  match: { label: "Khớp", icon: CircleCheck, fg: "text-done", bg: "bg-done-soft" },
  mismatch: { label: "Lệch", icon: TriangleAlert, fg: "text-mismatch", bg: "bg-mismatch-soft" },
  warning: { label: "Cần chú ý", icon: TriangleAlert, fg: "text-waiting", bg: "bg-waiting-soft" },
  disabled: { label: "Đang tắt", icon: PowerOff, fg: "text-skipped", bg: "bg-skipped-soft" },
};

export function StatusBadge({ status, label, className }: { status: Status; label?: string; className?: string }) {
  const def = STATUS[status];
  const Icon = def.icon;
  return (
    <span
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded-xs px-1.5 text-sm font-medium whitespace-nowrap",
        def.fg,
        def.bg,
        className,
      )}
    >
      <Icon className={cn("size-3.5 shrink-0", def.spin && "animate-spin motion-reduce:animate-none")} aria-hidden="true" />
      {label ?? def.label}
    </span>
  );
}

export function StatusIcon({ status, label }: { status: Status; label?: string }) {
  const def = STATUS[status];
  const Icon = def.icon;
  return (
    <span className={cn("inline-flex", def.fg)} title={label ?? def.label}>
      <Icon className="size-4" aria-hidden="true" />
      <span className="sr-only">{label ?? def.label}</span>
    </span>
  );
}
