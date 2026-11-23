import { Bot, ChevronDown, CircleCheck, Flag, Hand, Paperclip, Play, ShieldAlert, Square, SquareCheck } from "lucide-react";
import { cn } from "@/core/lib/cn";
import { StatusBadge, type Status } from "./status";

export interface FlowItem {
  id: string;
  text: string;
  checked?: boolean;
}

export interface FlowStep {
  key: string;
  title: string;
  kind: "auto" | "manual";
  description?: string;
  writes: string[];
  confirm: boolean;
  checklist: FlowItem[];
  evidenceRequired: boolean;
  status?: Status;
  statusLabel?: string;
}

const EDGE: Partial<Record<Status, string>> = {
  done: "var(--done)",
  in_progress: "var(--progress)",
  waiting: "var(--waiting)",
  warning: "var(--waiting)",
  failed: "var(--failed)",
  skipped: "var(--skipped)",
};

function Arrow() {
  return (
    <div aria-hidden="true" className="flex flex-col items-center text-line-strong">
      <span className="h-4 w-px bg-line-strong" />
      <ChevronDown className="-mt-1.5 size-4" />
    </div>
  );
}

function Terminal({ icon: Icon, label }: { icon: typeof Play; label: string }) {
  return (
    <div className="mx-auto flex w-fit items-center gap-2 rounded-full border border-line-strong bg-surface-2 px-4 py-1.5 text-sm font-semibold">
      <Icon className="size-4" aria-hidden="true" />
      {label}
    </div>
  );
}

/**
 * Vertical flowchart of a workflow: start → steps (with checklist) → operator check gates → end.
 * Rendered as an ordered list so screen readers get the same sequence the diagram shows.
 * With `status` on steps it doubles as a live view of a job.
 */
export function Flowchart({
  steps,
  verify,
  label,
  systemLabel = {},
}: {
  steps: FlowStep[];
  verify: boolean;
  label: string;
  systemLabel?: Record<string, string>;
}) {
  return (
    <div className="mx-auto max-w-2xl">
      <Terminal icon={Play} label="Bắt đầu" />
      <ol aria-label={label}>
        {steps.map((s, i) => (
          <li key={s.key}>
            <Arrow />
            <section
              aria-label={`Bước ${i + 1}: ${s.title}`}
              className={cn("rounded-md border bg-surface p-3", s.status === "waiting" || s.status === "failed" ? "border-2" : "border-line")}
              style={s.status === "waiting" || s.status === "failed" ? { borderColor: EDGE[s.status] } : undefined}
            >
              <header className="flex flex-wrap items-start justify-between gap-2">
                <p className="flex items-center gap-2 font-semibold">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-surface-2 text-sm tabular">{i + 1}</span>
                  {s.title}
                </p>
                {s.status && <StatusBadge status={s.status} label={s.statusLabel} />}
              </header>
              {s.description && <p className="mt-1 text-sm text-muted">{s.description}</p>}
              <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
                <span className="inline-flex items-center gap-1 rounded-xs bg-surface-2 px-1.5 py-0.5">
                  {s.kind === "auto" ? <Bot className="size-3.5" aria-hidden="true" /> : <Hand className="size-3.5" aria-hidden="true" />}
                  {s.kind === "auto" ? "Tự động" : "Thủ công"}
                </span>
                {s.writes.map((w) => (
                  <span key={w} className="rounded-xs border border-line px-1.5 py-0.5">Ghi vào {systemLabel[w] ?? w}</span>
                ))}
                {s.confirm && (
                  <span className="inline-flex items-center gap-1 rounded-xs bg-failed-soft px-1.5 py-0.5 text-failed">
                    <ShieldAlert className="size-3.5" aria-hidden="true" />Nhập lại Mã NV trước khi chạy
                  </span>
                )}
              </div>
              {verify && (
                <div className="mt-3 border-t border-line pt-2">
                  <p className="mb-1 flex flex-wrap items-center gap-2 text-sm font-semibold text-muted">
                    Checklist kiểm tra
                    {s.evidenceRequired && (
                      <span className="inline-flex items-center gap-1 rounded-xs bg-primary-soft px-1.5 py-0.5 font-medium text-primary normal-case">
                        <Paperclip className="size-3.5" aria-hidden="true" />Cần bằng chứng
                      </span>
                    )}
                  </p>
                  {s.checklist.length ? (
                    <ul className="space-y-0.5 text-sm">
                      {s.checklist.map((c) => (
                        <li key={c.id} className="flex items-start gap-1.5">
                          {!s.status ? (
                            <span aria-hidden="true" className="mt-2 size-1.5 shrink-0 rounded-full bg-muted" />
                          ) : c.checked ? (
                            <SquareCheck className="mt-0.5 size-4 shrink-0 text-done" aria-hidden="true" />
                          ) : (
                            <Square className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
                          )}
                          {s.status && <span className="sr-only">{c.checked ? "Đã kiểm tra:" : "Chưa kiểm tra:"}</span>}
                          {c.text}
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-sm text-muted">Không có mục – Operator chỉ xác nhận hoàn tất.</p>
                  )}
                </div>
              )}
            </section>
            {verify && (
              <div className="flex items-center justify-center gap-3 pt-0">
                <div aria-hidden="true" className="flex flex-col items-center">
                  <span className="h-3 w-px bg-line-strong" />
                  <span className="flex size-7 rotate-45 items-center justify-center border border-primary bg-primary-soft">
                    <CircleCheck className="size-3.5 -rotate-45 text-primary" />
                  </span>
                </div>
                <p className="text-xs text-muted">
                  <span className="font-medium text-ink">Operator kiểm tra</span> · Đạt → bước tiếp theo · Không đạt → chạy lại bước
                </p>
              </div>
            )}
          </li>
        ))}
      </ol>
      <Arrow />
      <Terminal icon={Flag} label="Hoàn tất" />
    </div>
  );
}
