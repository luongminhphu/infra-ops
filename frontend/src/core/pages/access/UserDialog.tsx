import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import type { AppUser, Role } from "@/core/api/types";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input, Switch } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";

export function UserDialog({ open, onClose, user, roles }: { open: boolean; onClose: () => void; user: AppUser | null; roles: Role[] }) {
  const qc = useQueryClient();
  const { me } = useAuth();
  const isNew = !user;
  const localMode = me?.auth_mode === "local";
  const localLogin = !!me?.local_login; // SSO + local accounts: the password is optional
  const [upn, setUpn] = useState("");
  const [name, setName] = useState("");
  const [roleIds, setRoleIds] = useState<string[]>([]);
  const [active, setActive] = useState(true);
  const [password, setPassword] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [serverError, setServerError] = useState("");

  useEffect(() => {
    if (!open) return;
    setUpn(user?.upn ?? "");
    setName(user?.display_name ?? "");
    setRoleIds(user?.role_ids ?? []);
    setActive(user?.is_active ?? true);
    setPassword("");
    setSubmitted(false);
    setServerError("");
  }, [open, user]);

  const needsPassword = isNew && localMode;
  const errors = {
    upn: !upn.trim() ? "Nhập tên đăng nhập hoặc email." : undefined,
    name: !name.trim() ? "Nhập họ tên." : undefined,
    password: needsPassword && !password ? "Đặt mật khẩu ban đầu." : undefined,
  };

  const save = useMutation({
    mutationFn: () =>
      isNew
        ? api("/api/core/users", { method: "POST", json: { upn: upn.trim(), display_name: name.trim(), role_ids: roleIds, password: password || null } })
        : api(`/api/core/users/${user!.id}`, {
            method: "PATCH",
            json: { display_name: name.trim(), role_ids: roleIds, is_active: active, password: password || null },
          }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["users"] });
      await qc.invalidateQueries({ queryKey: ["roles"] });
      toast.success(isNew ? "Đã thêm người dùng." : "Đã lưu thay đổi người dùng.");
      onClose();
    },
    onError: (e) => {
      setServerError(errorMessage(e));
      announce(errorMessage(e));
    },
  });

  const unlock = useMutation({
    mutationFn: () => api(`/api/core/users/${user!.id}`, { method: "PATCH", json: { unlock: true } }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["users"] });
      toast.success("Đã mở khoá tài khoản.");
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (Object.values(errors).some(Boolean)) {
      announce("Vui lòng sửa các trường được đánh dấu.");
      return;
    }
    save.mutate();
  };

  return (
    <Dialog open={open} onClose={onClose} title={isNew ? "Thêm người dùng" : `Sửa ${user?.display_name}`} size="md">
      <form onSubmit={submit} noValidate className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label={localMode ? "Tên đăng nhập" : "UPN (email Microsoft)"} required error={errors.upn} showError={submitted}>
            <Input value={upn} onChange={(e) => setUpn(e.target.value)} disabled={!isNew} autoComplete="off" spellCheck={false} />
          </Field>
          <Field label="Họ tên" required error={errors.name} showError={submitted}>
            <Input value={name} onChange={(e) => setName(e.target.value)} />
          </Field>
        </div>
        <fieldset>
          <legend className="mb-1.5 text-sm font-medium">Vai trò</legend>
          <div className="space-y-1.5">
            {roles.map((r) => (
              <label key={r.id} className="flex cursor-pointer items-start gap-2.5 rounded-sm px-1 py-1 hover:bg-surface-2">
                <input
                  type="checkbox"
                  className="mt-1 size-4 accent-[var(--primary)]"
                  checked={roleIds.includes(r.id)}
                  onChange={(e) => setRoleIds((ids) => (e.target.checked ? [...ids, r.id] : ids.filter((x) => x !== r.id)))}
                />
                <span>
                  <span className="font-medium">{r.name}</span>
                  <span className="block text-sm text-muted">{r.description}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        {(needsPassword || (isNew && localLogin) || (!isNew && (user?.is_local || localLogin))) && (
          <Field
            label={isNew ? "Mật khẩu ban đầu" : user?.is_local ? "Đặt lại mật khẩu" : "Mật khẩu tài khoản nội bộ"}
            required={needsPassword}
            hint={
              !localMode
                ? "Tuỳ chọn. Để trống: chỉ đăng nhập bằng Microsoft SSO. Đặt mật khẩu: người này đăng nhập được thêm bằng tài khoản nội bộ (tối thiểu 12 ký tự, 3 loại ký tự)."
                : isNew
                  ? "Tối thiểu 12 ký tự, gồm ít nhất 3 loại ký tự. Gửi cho người dùng qua kênh an toàn."
                  : "Để trống nếu không đổi. Người dùng sẽ bị đăng xuất khỏi mọi phiên."
            }
            error={errors.password}
            showError={submitted}
          >
            <Input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" />
          </Field>
        )}
        {!isNew && (
          <Switch
            label="Cho phép đăng nhập"
            description={user?.id === me?.id ? "Bạn không thể tự vô hiệu hoá tài khoản của mình." : "Tắt để chặn đăng nhập và kết thúc mọi phiên đang mở."}
            checked={active}
            onChange={setActive}
            disabled={user?.id === me?.id}
          />
        )}
        {user?.locked && (
          <div className="flex items-center justify-between gap-3 rounded-sm bg-waiting-soft px-3 py-2 text-sm">
            <span>Tài khoản đang tạm khoá do đăng nhập sai nhiều lần.</span>
            <Button size="sm" onClick={() => unlock.mutate()} loading={unlock.isPending}>Mở khoá</Button>
          </div>
        )}
        {serverError && <p role="alert" className="rounded-sm bg-failed-soft px-2.5 py-2 text-sm text-failed">{serverError}</p>}
        <div className="flex justify-end gap-2 border-t border-line pt-3">
          <Button onClick={onClose}>Huỷ</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>{isNew ? "Thêm người dùng" : "Lưu thay đổi"}</Button>
        </div>
      </form>
    </Dialog>
  );
}
