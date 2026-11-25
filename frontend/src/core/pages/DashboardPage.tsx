import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight, CalendarClock, ChevronRight, PartyPopper, Plug } from "lucide-react";
import { api } from "@/core/api/client";
import type { DashboardWidget } from "@/core/api/types";
import { useAuth } from "@/core/auth/AuthProvider";
import { EmptyState, ErrorState, LoadingRows, PageHeader, Panel, Skeleton } from "@/core/ui/feedback";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge, type Status } from "@/core/ui/status";
import { formatDate, formatRelative, pluralDays } from "@/core/lib/format";
import { modules } from "@/modules";
import { LEVEL, useNextActions } from "./MyWorkPage";

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
  const hot = !!tone && value > 0;
  return (
    // Only a non-zero problem count is highlighted (tinted card + colour), so zeros stay calm.
    <Link
      to="/settings/connections"
      className="block rounded-md border border-line bg-surface px-4 py-3 shadow-card hover:bg-surface-2"
      style={hot ? { borderColor: `var(--${tone})`, background: `var(--${tone}-soft)` } : undefined}
    >
      <span className={`block text-sm ${hot ? "text-ink" : "text-muted"}`}>{label}</span>
      <span className={`tabular mt-1 block text-2xl font-semibold ${hot ? (tone === "failed" ? "text-failed" : "text-waiting") : ""}`}>
        {value}
      </span>
    </Link>
  );
}

/** The cross-module "Việc tiếp theo" queue (same as Việc của tôi), first on the home page: what needs you now. */
function NextActions() {
  const q = useNextActions();
  const items = q.data?.items ?? [];
  return (
    <Panel
      title="Việc tiếp theo của bạn"
      description="Job, task quá hạn, đề xuất mua cần duyệt… việc gấp nhất trên cùng."
      actions={items.length > 3 ? <Link to="/me/work" className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline">Xem tất cả ({items.length})<ArrowRight className="size-4" aria-hidden="true" /></Link> : undefined}
      className="mb-4"
      bodyClassName="p-0"
    >
      {q.isError ? (
        <div className="p-4"><ErrorState error={q.error} onRetry={() => q.refetch()} /></div>
      ) : q.isLoading ? (
        <LoadingRows rows={3} />
      ) : items.length === 0 ? (
        <EmptyState icon={PartyPopper} title="Không còn việc nào cần bạn làm">Việc mới sẽ hiện ở đây và ở trang Việc của tôi.</EmptyState>
      ) : (
        <ul className="divide-y divide-line">
          {items.slice(0, 3).map((i) => (
            <li key={i.key}>
              <Link to={i.href} className="flex items-center gap-3 px-4 py-2.5 hover:bg-surface-2">
                <StatusBadge {...LEVEL[i.priority]!} />
                <span className="min-w-0 flex-1">
                  <span className="block font-medium">{i.title}</span>
                  {(i.detail || i.due) && (
                    <span className="block truncate text-sm text-muted">
                      {i.detail}{i.detail && i.due ? " · " : ""}{i.due ? `hạn ${formatDate(i.due)}` : ""}
                    </span>
                  )}
                </span>
                <span className="hidden shrink-0 text-sm font-medium text-primary sm:inline">{i.action}</span>
                <ChevronRight className="size-4 shrink-0 text-muted" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Panel>
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
          {c.last_test_at && <span className="hidden text-sm text-muted sm:block">Kiểm tra {formatRelative(c.last_test_at)}</span>}
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
      <EmptyState icon={CalendarClock} title="Không có mật khẩu / chứng chỉ sắp hết hạn">
        Cảnh báo hiển thị khi mật khẩu, API key hoặc chứng chỉ của kết nối còn dưới {data.warn_days} ngày.
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
    { to: "/settings/access", title: "Tạo tài khoản và gán vai trò", detail: "Viewer, Operator, Supervisor hoặc Manager cho từng người trong phòng IT." },
    { to: "/lifecycle/settings", title: "Cấu hình module", detail: "Nhãn trường RTR, nhóm BioStar mặc định; liên kết Operator với technician ở Cấu hình công việc." },
    { to: "/lifecycle/sync", title: "Đồng bộ dữ liệu lần đầu", detail: "Đọc request, user, tài sản và user BioStar để bắt đầu đối soát." },
  ];
  return (
    <Panel title="Bắt đầu sử dụng" description="Chưa có kết nối hệ thống nào – làm 4 bước dưới đây để infra-ops đọc dữ liệu thật từ ServiceDesk Plus và BioStar." className="mb-4">
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
  // System plumbing (connection health) is for the people who can fix it, after the work.
  const admin = can("core.connections.manage");

  return (
    <>
      <PageHeader
        title="Tổng quan"
        description={
          <>
            Việc cần bạn làm tiếp theo, rồi tình hình Helpdesk, Job và đối soát của nhóm.{" "}
            <HelpTip title="Tổng quan">
              <span className="block">“Việc tiếp theo của bạn” là việc của riêng bạn (giống trang Việc của tôi). Các khối bên dưới là số liệu của cả nhóm theo quyền của bạn; bấm vào số hoặc dòng để mở danh sách đã lọc.</span>
            </HelpTip>
          </>
        }
      />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {firstRun && <GettingStarted />}
      {!firstRun && <NextActions />}
      <div className="grid gap-4 xl:grid-cols-2 [&>*]:min-w-0">
        {q.data?.filter((w) => !((firstRun || !admin) && ["connections", "expiring_secrets"].includes(w.kind))).map((w) => (
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
      {admin && conns && conns.items.length > 0 && (
        <section aria-label="Tình trạng kết nối" className="mt-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
          <Tile label="Kết nối hệ thống" value={conns.items.length} />
          <Tile label="Kết nối đang lỗi" value={conns.items.filter((c) => c.state === "error").length} tone="failed" />
          <Tile label="Cần cấu hình hoặc kiểm tra" value={conns.items.filter((c) => c.state === "untested" || c.state === "incomplete").length} tone="waiting" />
          <Tile label="Mật khẩu / chứng chỉ sắp hết hạn" value={exp?.items.length ?? 0} tone="waiting" />
        </section>
      )}
    </>
  );
}

