import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, Plug } from "lucide-react";
import { api } from "@/core/api/client";
import type { DashboardWidget } from "@/core/api/types";
import { useAuth } from "@/core/auth/AuthProvider";
import { EmptyState, ErrorState, PageHeader, Panel, Skeleton } from "@/core/ui/feedback";
import { StatusBadge, type Status } from "@/core/ui/status";
import { formatDate, formatRelative, pluralDays } from "@/core/lib/format";

interface ConnItem {
  id: string;
  name: string;
  system_name: string;
  state: "ok" | "error" | "untested" | "disabled" | "incomplete";
  read: boolean;
  write: boolean;
  last_test_at: string | null;
}

const CONN_STATE: Record<ConnItem["state"], { status: Status; label: string }> = {
  ok: { status: "done", label: "Hoạt động" },
  error: { status: "failed", label: "Lỗi kết nối" },
  untested: { status: "pending", label: "Chưa kiểm tra" },
  disabled: { status: "disabled", label: "Đang tắt" },
  incomplete: { status: "warning", label: "Thiếu credential" },
};

function Tile({ label, value, tone }: { label: string; value: number; tone?: "failed" | "waiting" }) {
  return (
    <div className="rounded-md border border-line bg-surface px-4 py-3">
      <p className="text-sm text-muted">{label}</p>
      <p className={`tabular mt-1 text-2xl font-semibold ${tone === "failed" ? "text-failed" : tone === "waiting" ? "text-waiting" : ""}`}>
        {value}
      </p>
    </div>
  );
}

function ConnectionsWidget({ data }: { data: { items: ConnItem[] } }) {
  const { can } = useAuth();
  if (data.items.length === 0)
    return (
      <EmptyState icon={Plug} title="Chưa có kết nối nào">
        {can("core.connections.manage") ? (
          <>
            Thêm kết nối tới ServiceDesk Plus và BioStar để bắt đầu. <Link className="text-primary underline" to="/settings/connections">Mở Kết nối hệ thống</Link>
          </>
        ) : (
          "Quản trị viên chưa cấu hình kết nối hệ thống."
        )}
      </EmptyState>
    );
  return (
    <ul className="divide-y divide-line">
      {data.items.map((c) => (
        <li key={c.id} className="flex items-center gap-3 py-2">
          <div className="min-w-0 flex-1">
            <Link to={`/settings/connections/${c.id}`} className="font-medium hover:text-primary hover:underline">
              {c.name}
            </Link>
            <p className="text-sm text-muted">
              {c.system_name} · Read {c.read ? "có" : "chưa có"} · Write {c.write ? "có" : "chưa có"}
            </p>
          </div>
          <span className="hidden text-sm text-muted sm:block">{formatRelative(c.last_test_at)}</span>
          <StatusBadge status={CONN_STATE[c.state].status} label={CONN_STATE[c.state].label} />
        </li>
      ))}
    </ul>
  );
}

interface ExpItem {
  profile_id: string;
  name: string;
  purpose: string;
  expires_at: string;
  days_left: number;
}

function ExpiringWidget({ data }: { data: { warn_days: number; items: ExpItem[] } }) {
  if (data.items.length === 0)
    return (
      <EmptyState icon={CalendarClock} title="Không có secret sắp hết hạn">
        Cảnh báo hiển thị khi secret hoặc chứng chỉ còn dưới {data.warn_days} ngày.
      </EmptyState>
    );
  return (
    <ul className="divide-y divide-line">
      {data.items.map((i) => (
        <li key={`${i.profile_id}-${i.purpose}`} className="flex items-center gap-3 py-2">
          <div className="min-w-0 flex-1">
            <Link to={`/settings/connections/${i.profile_id}`} className="font-medium hover:text-primary hover:underline">
              {i.name}
            </Link>
            <p className="text-sm text-muted">
              Credential {i.purpose === "read" ? "Read" : "Write"} · hết hạn {formatDate(i.expires_at)}
            </p>
          </div>
          <StatusBadge status={i.days_left < 0 ? "failed" : "warning"} label={pluralDays(i.days_left)} />
        </li>
      ))}
    </ul>
  );
}

export function DashboardPage() {
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<DashboardWidget[]>("/api/core/dashboard") });
  const conns = q.data?.find((w) => w.key === "core.connections")?.data as { items: ConnItem[] } | undefined;
  const exp = q.data?.find((w) => w.key === "core.expiring_secrets")?.data as { items: ExpItem[] } | undefined;

  return (
    <>
      <PageHeader title="Tổng quan" description="Tình trạng kết nối hệ thống và các việc cần chú ý." />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {q.isLoading ? (
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-[78px]" />)
        ) : (
          <>
            <Tile label="Kết nối hệ thống" value={conns?.items.length ?? 0} />
            <Tile label="Kết nối đang lỗi" value={conns?.items.filter((c) => c.state === "error").length ?? 0} tone="failed" />
            <Tile label="Cần cấu hình hoặc kiểm tra" value={conns?.items.filter((c) => c.state === "untested" || c.state === "incomplete").length ?? 0} tone="waiting" />
            <Tile label="Secret sắp hết hạn" value={exp?.items.length ?? 0} tone="waiting" />
          </>
        )}
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        {q.data?.map((w) => (
          <Panel key={w.key} title={w.title} bodyClassName="py-1">
            {w.error ? (
              <ErrorState error={new Error(w.error)} />
            ) : w.kind === "connections" ? (
              <ConnectionsWidget data={w.data as { items: ConnItem[] }} />
            ) : w.kind === "expiring_secrets" ? (
              <ExpiringWidget data={w.data as { warn_days: number; items: ExpItem[] }} />
            ) : (
              <p className="py-4 text-sm text-muted">Widget “{w.kind}” chưa có giao diện.</p>
            )}
          </Panel>
        ))}
        {q.isLoading && (
          <>
            <Skeleton className="h-48" />
            <Skeleton className="h-48" />
          </>
        )}
      </div>
    </>
  );
}
