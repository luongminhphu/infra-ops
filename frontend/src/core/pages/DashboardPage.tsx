import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { CalendarClock, ChevronRight, Plug } from "lucide-react";
import { api } from "@/core/api/client";
import type { DashboardWidget } from "@/core/api/types";
import { useAuth } from "@/core/auth/AuthProvider";
import { EmptyState, ErrorState, PageHeader, Panel, Skeleton } from "@/core/ui/feedback";
import { StatusBadge, type Status } from "@/core/ui/status";
import { formatDate, formatRelative, pluralDays } from "@/core/lib/format";
import { modules } from "@/modules";

const moduleWidgets = Object.assign({}, ...modules.map((m) => m.widgets ?? {})) as Record<
  string,
  React.ComponentType<{ data: unknown }>
>;

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
    // Only a non-zero problem count is highlighted (tinted card + colour), so zeros stay calm.
    <div
      className="rounded-md border border-line bg-surface px-4 py-3 shadow-card"
      style={tone && value > 0 ? { borderColor: `var(--${tone})`, background: `var(--${tone}-soft)` } : undefined}
    >
      <p className="text-sm text-muted">{label}</p>
      <p className={`tabular mt-1 text-2xl font-semibold ${value > 0 && tone === "failed" ? "text-failed" : value > 0 && tone === "waiting" ? "text-waiting" : ""}`}>
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

/** First run: what an admin does before the dashboard has anything to show. */
function GettingStarted() {
  const steps = [
    { to: "/settings/connections", title: "Thêm kết nối ServiceDesk Plus và BioStar", detail: "Nhập credential Read (đồng bộ) và Write (chạy Job), bấm Kiểm tra kết nối." },
    { to: "/settings/access", title: "Tạo tài khoản và gán vai trò", detail: "Manager, Supervisor, Operator hoặc Chỉ xem cho từng người trong phòng IT." },
    { to: "/lifecycle/settings", title: "Cấu hình module", detail: "Nhãn trường RTR, nhóm BioStar mặc định; liên kết Operator với technician ở Cấu hình công việc." },
    { to: "/lifecycle/sync", title: "Đồng bộ dữ liệu lần đầu", detail: "Đọc request, user, tài sản và user BioStar để bắt đầu đối soát." },
  ];
  return (
    <Panel title="Bắt đầu sử dụng" description="Chưa có kết nối hệ thống nào, nên các số liệu bên dưới đều trống." className="mb-4">
      <ol className="grid gap-3 md:grid-cols-2">
        {steps.map((s, i) => (
          <li key={s.to}>
            <Link to={s.to} className="flex h-full gap-3 rounded-sm border border-line p-3 hover:bg-surface-2">
              <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary-soft text-sm font-semibold text-primary tabular">{i + 1}</span>
              <span className="min-w-0 flex-1">
                <span className="block font-medium text-primary">{s.title}</span>
                <span className="block text-sm text-muted">{s.detail}</span>
              </span>
              <ChevronRight className="mt-0.5 size-4 shrink-0 text-muted" aria-hidden="true" />
            </Link>
          </li>
        ))}
      </ol>
    </Panel>
  );
}

export function DashboardPage() {
  const { can } = useAuth();
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<DashboardWidget[]>("/api/core/dashboard") });
  const conns = q.data?.find((w) => w.key === "core.connections")?.data as { items: ConnItem[] } | undefined;
  const exp = q.data?.find((w) => w.key === "core.expiring_secrets")?.data as { items: ExpItem[] } | undefined;
  // Nothing connected yet: show the setup steps instead of empty connection panels.
  const firstRun = !!conns && conns.items.length === 0 && can("core.connections.manage");

  return (
    <>
      <PageHeader title="Tổng quan" description="Việc cần làm tiếp theo và tình trạng các hệ thống kết nối." />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {firstRun && <GettingStarted />}
      {(q.isLoading || (conns && conns.items.length > 0)) && <div className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
        {q.isLoading ? (
          Array.from({ length: 4 }, (_, i) => <Skeleton key={i} className="h-[78px]" />)
        ) : (
          <>
            <Tile label="Kết nối hệ thống" value={conns?.items.length ?? 0} />
            <Tile label="Kết nối đang lỗi" value={conns?.items.filter((c) => c.state === "error").length ?? 0} tone="failed" />
            <Tile label="Cần cấu hình hoặc kiểm tra" value={conns?.items.filter((c) => c.state === "untested" || c.state === "incomplete").length ?? 0} tone="waiting" />
            <Tile label="Mật khẩu / chứng chỉ sắp hết hạn" value={exp?.items.length ?? 0} tone="waiting" />
          </>
        )}
      </div>}
      <div className="grid gap-4 xl:grid-cols-2 [&>*]:min-w-0">
        {q.data?.filter((w) => !(firstRun && ["connections", "expiring_secrets"].includes(w.kind))).map((w) => (
          <Panel key={w.key} title={w.title} bodyClassName="py-1" className={["lifecycle_reconciliation", "lifecycle_jobs", "workload_next"].includes(w.kind) ? "xl:col-span-2" : undefined}>
            {w.error ? (
              <ErrorState error={new Error(w.error)} />
            ) : w.kind === "connections" ? (
              <ConnectionsWidget data={w.data as { items: ConnItem[] }} />
            ) : w.kind === "expiring_secrets" ? (
              <ExpiringWidget data={w.data as { warn_days: number; items: ExpItem[] }} />
            ) : moduleWidgets[w.kind] ? (
              (() => {
                const Widget = moduleWidgets[w.kind]!;
                return <Widget data={w.data} />;
              })()
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
