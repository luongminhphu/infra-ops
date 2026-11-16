import { CircleCheck, CircleHelp, CircleX } from "lucide-react";
import type { TestResult } from "@/core/api/types";
import { StatusIcon, type Status } from "@/core/ui/status";
import { formatDateTime } from "@/core/lib/format";

const STEP: Record<string, Status> = { ok: "done", warn: "warning", fail: "failed", skip: "skipped" };
const STEP_LABEL: Record<string, string> = { ok: "Đạt", warn: "Cảnh báo", fail: "Lỗi", skip: "Bỏ qua" };

export function TestResultView({ result }: { result: TestResult }) {
  return (
    <div className="space-y-4">
      <p className="text-sm text-muted">
        {result.ok ? "Kết nối thành công" : "Kết nối chưa thành công"}
        {result.tested_at && ` · kiểm tra lúc ${formatDateTime(result.tested_at)}`}
        {typeof result.info.role === "string" && ` · quyền tài khoản: ${result.info.role}`}
      </p>
      <ol className="space-y-2">
        {result.steps.map((s, i) => (
          <li key={`${s.key}-${i}`} className="flex gap-2.5">
            <StatusIcon status={STEP[s.status] ?? "pending"} label={STEP_LABEL[s.status]} />
            <div className="min-w-0 flex-1">
              <p className="font-medium">{s.label}</p>
              {s.detail && <p className="text-sm break-words text-muted">{s.detail}</p>}
              {s.hint && (
                <p className="mt-1 rounded-xs bg-surface-2 px-2 py-1 text-sm">
                  <span className="font-medium">Cách khắc phục: </span>
                  {s.hint}
                </p>
              )}
            </div>
          </li>
        ))}
      </ol>
      {result.permissions.length > 0 && (
        <div>
          <h3 className="mb-1.5 text-sm font-semibold">Quyền thực tế</h3>
          <ul className="divide-y divide-line rounded-sm border border-line">
            {result.permissions.map((p) => (
              <li key={p.name} className="flex items-start gap-2 px-2.5 py-1.5 text-sm">
                {p.granted === true ? (
                  <CircleCheck className="mt-0.5 size-4 shrink-0 text-done" aria-hidden="true" />
                ) : p.granted === false ? (
                  <CircleX className="mt-0.5 size-4 shrink-0 text-failed" aria-hidden="true" />
                ) : (
                  <CircleHelp className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
                )}
                <span className="sr-only">{p.granted === true ? "Có quyền" : p.granted === false ? "Không có quyền" : "Chưa xác định"}:</span>
                <span className="flex-1">{p.name}</span>
                <span className="text-right text-muted">{p.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
