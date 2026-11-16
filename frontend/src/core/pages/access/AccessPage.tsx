import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Lock, Plus, ShieldCheck, Users } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import type { AppUser, PermissionGroup, Role } from "@/core/api/types";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { EmptyState, ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { StatusBadge } from "@/core/ui/status";
import { Tabs } from "@/core/ui/tabs";
import { formatRelative } from "@/core/lib/format";
import { UserDialog } from "./UserDialog";
import { RoleDialog } from "./RoleDialog";

export function useAccessData() {
  const users = useQuery({ queryKey: ["users"], queryFn: () => api<AppUser[]>("/api/core/users") });
  const roles = useQuery({ queryKey: ["roles"], queryFn: () => api<Role[]>("/api/core/roles") });
  const perms = useQuery({ queryKey: ["permissions"], queryFn: () => api<PermissionGroup[]>("/api/core/permissions") });
  return { users, roles, perms };
}

function UsersTab({ users, roles }: { users: AppUser[]; roles: Role[] }) {
  const { me } = useAuth();
  const [editing, setEditing] = useState<AppUser | null | "new">(null);
  const roleName = useMemo(() => Object.fromEntries(roles.map((r) => [r.id, r.name])), [roles]);
  return (
    <>
      <div className="mb-3 flex justify-end">
        <Button variant="primary" onClick={() => setEditing("new")}>
          <Plus className="size-4" aria-hidden="true" />
          Thêm người dùng
        </Button>
      </div>
      {users.length === 0 ? (
        <EmptyState icon={Users} title="Chưa có người dùng" />
      ) : (
        <div className="overflow-x-auto rounded-md border border-line bg-surface">
          <table className="w-full text-left">
            <caption className="sr-only">Người dùng infra-ops</caption>
            <thead className="bg-surface-2 text-sm text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 font-semibold">Người dùng</th>
                <th scope="col" className="px-3 py-2 font-semibold">Vai trò</th>
                <th scope="col" className="px-3 py-2 font-semibold">Đăng nhập gần nhất</th>
                <th scope="col" className="px-3 py-2 font-semibold">Trạng thái</th>
                <th scope="col" className="px-3 py-2"><span className="sr-only">Thao tác</span></th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-line" style={{ height: "var(--row-h-comfortable)" }}>
                  <td className="px-3 py-1.5">
                    <p className="font-medium">{u.display_name}{u.id === me?.id && <span className="ml-1.5 text-xs text-muted">(bạn)</span>}</p>
                    <p className="text-sm text-muted">{u.upn} · {u.is_local ? "Tài khoản nội bộ" : me?.auth_mode === "dev" ? "Đăng nhập chế độ phát triển" : "Microsoft SSO"}</p>
                  </td>
                  <td className="px-3 py-1.5 text-base">{u.role_ids.map((id) => roleName[id]).filter(Boolean).join(", ") || <span className="text-muted">Chưa gán</span>}</td>
                  <td className="px-3 py-1.5 text-base text-muted">{formatRelative(u.last_login_at)}</td>
                  <td className="px-3 py-1.5">
                    {!u.is_active ? <StatusBadge status="disabled" label="Đã vô hiệu hoá" /> : u.locked ? <StatusBadge status="warning" label="Tạm khoá" /> : <StatusBadge status="done" label="Hoạt động" />}
                  </td>
                  <td className="px-3 py-1.5 text-right">
                    <Button size="sm" onClick={() => setEditing(u)} aria-label={`Sửa ${u.display_name}`}>Sửa</Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <UserDialog open={editing !== null} onClose={() => setEditing(null)} user={editing === "new" ? null : editing} roles={roles} />
    </>
  );
}

function RolesTab({ roles, perms }: { roles: Role[]; perms: PermissionGroup[] }) {
  const [editing, setEditing] = useState<Role | null | "new">(null);
  const qc = useQueryClient();
  const remove = useMutation({
    mutationFn: (id: string) => api(`/api/core/roles/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["roles"] });
      toast.success("Đã xoá vai trò.");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const total = perms.reduce((n, g) => n + g.permissions.length, 0);
  return (
    <>
      <div className="mb-3 flex justify-end">
        <Button variant="primary" onClick={() => setEditing("new")}>
          <Plus className="size-4" aria-hidden="true" />
          Tạo vai trò
        </Button>
      </div>
      <div className="grid gap-3 lg:grid-cols-3">
        {roles.map((r) => (
          <Panel
            key={r.id}
            title={
              <span className="inline-flex items-center gap-2">
                {r.name}
                {r.is_system && <span className="rounded-xs bg-surface-2 px-1.5 text-xs font-normal text-muted">Hệ thống</span>}
              </span>
            }
            description={r.description}
            bodyClassName="py-3"
          >
            <p className="text-sm text-muted">
              {r.all_permissions ? "Toàn quyền (không chỉnh được)" : `${r.permissions.length}/${total} quyền`} · {r.user_count} người dùng
            </p>
            <div className="mt-3 flex gap-2">
              <Button size="sm" onClick={() => setEditing(r)}>
                {r.all_permissions ? <Lock className="size-3.5" aria-hidden="true" /> : null}
                {r.all_permissions ? "Xem quyền" : "Sửa quyền"}
              </Button>
              {!r.is_system && (
                <Button size="sm" variant="ghost" className="text-failed" onClick={() => remove.mutate(r.id)} disabled={r.user_count > 0} title={r.user_count > 0 ? "Gỡ vai trò khỏi người dùng trước khi xoá" : undefined}>
                  Xoá
                </Button>
              )}
            </div>
          </Panel>
        ))}
      </div>
      <RoleDialog open={editing !== null} onClose={() => setEditing(null)} role={editing === "new" ? null : editing} groups={perms} />
    </>
  );
}

export function AccessPage() {
  const [tab, setTab] = useState("users");
  const { users, roles, perms } = useAccessData();
  const loading = users.isLoading || roles.isLoading || perms.isLoading;
  const error = users.error ?? roles.error ?? perms.error;
  return (
    <>
      <PageHeader title="Người dùng & quyền" description="Ai được đăng nhập infra-ops và được làm gì trong từng module." />
      {error && <ErrorState error={error} />}
      {loading ? (
        <LoadingRows rows={5} />
      ) : (
        users.data && roles.data && perms.data && (
          <Tabs
            label="Quản lý quyền"
            value={tab}
            onChange={setTab}
            tabs={[
              { key: "users", label: <span className="inline-flex items-center gap-1.5"><Users className="size-4" aria-hidden="true" />Người dùng ({users.data.length})</span> },
              { key: "roles", label: <span className="inline-flex items-center gap-1.5"><ShieldCheck className="size-4" aria-hidden="true" />Vai trò ({roles.data.length})</span> },
            ]}
          >
            {tab === "users" ? <UsersTab users={users.data} roles={roles.data} /> : <RolesTab roles={roles.data} perms={perms.data} />}
          </Tabs>
        )
      )}
    </>
  );
}
