import type { ConnectionProfile, ConnectionStatus as CS, CredentialSummary } from "@/core/api/types";
import { StatusBadge, type Status } from "@/core/ui/status";
import { formatDate, pluralDays } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";

export const PROFILE_STATUS: Record<CS, Status> = {
  ok: "done",
  warning: "warning",
  error: "failed",
  untested: "pending",
  disabled: "disabled",
  incomplete: "warning",
};

export const STATUS_BAR: Record<CS, string> = {
  ok: "bg-done",
  warning: "bg-waiting",
  error: "bg-failed",
  untested: "bg-line-strong",
  disabled: "bg-line-strong",
  incomplete: "bg-waiting",
};

export function ProfileStatusBadge({ profile }: { profile: ConnectionProfile }) {
  return <StatusBadge status={PROFILE_STATUS[profile.status]} label={profile.status_text} />;
}

/** One-line summary of a credential purpose for lists. */
export function CredentialLine({ purpose, cred }: { purpose: "read" | "write"; cred: CredentialSummary | null }) {
  const name = purpose === "read" ? "Read" : "Write";
  if (!cred)
    return (
      <span className="text-sm text-muted">
        {name}: <span className="italic">chưa cấu hình</span>
        {purpose === "write" && " – thao tác ghi bị khoá"}
      </span>
    );
  const status: Status =
    cred.last_test_ok === false ? "failed" : cred.days_left !== null && cred.days_left < 0 ? "failed" : cred.expiring ? "warning" : cred.last_test_ok ? "done" : "pending";
  // Show why the last test failed right in the list, so users need not open the detail page.
  const fail = cred.last_test_ok === false ? cred.last_test_result?.steps.find((s) => s.status === "fail") : undefined;
  const failedStep = fail ? `${fail.label}: ${fail.detail}` : "";
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-sm">
      <StatusBadge
        status={status}
        label={name}
        className={cn(status === "pending" && "text-ink")}
      />
      {cred.hint && <span className="font-mono text-muted">••••{cred.hint}</span>}
      {cred.expires_at && (
        <span className={cn(cred.expiring ? "text-waiting" : "text-muted")}>
          hết hạn {formatDate(cred.expires_at)}
          {cred.days_left !== null && cred.days_left <= 60 && ` (${pluralDays(cred.days_left)})`}
        </span>
      )}
      {failedStep && <span className="text-failed">{failedStep}</span>}
    </span>
  );
}
