import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import type { PermissionGroup, Role } from "@/core/api/types";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";
import { fold } from "@/core/layout/CommandPalette";

function slug(name: string): string {
  return fold(name).replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "").slice(0, 60) || "role";
}

export function RoleDialog({ open, onClose, role, groups }: { open: boolean; onClose: () => void; role: Role | null; groups: PermissionGroup[] }) {
  const qc = useQueryClient();
  const isNew = !role;
  const locked = Boolean(role?.all_permissions);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [perms, setPerms] = useState<Set<string>>(new Set());
  const [submitted, setSubmitted] = useState(false);
  const [serverError, setServerError] = useState("");

  useEffect(() => {
    if (!open) return;
    setName(role?.name ?? "");
    setDescription(role?.description ?? "");
    setPerms(new Set(role?.permissions ?? []));
    setSubmitted(false);
    setServerError("");
  }, [open, role]);

  const toggle = (code: string, on: boolean) =>
    setPerms((s) => {
      const n = new Set(s);
      if (on) n.add(code);
      else n.delete(code);
      return n;
    });

  const save = useMutation({
    mutationFn: () =>
      isNew
        ? api("/api/core/roles", { method: "POST", json: { code: slug(name), name: name.trim(), description, permissions: [...perms] } })
        : api(`/api/core/roles/${role!.id}`, { method: "PATCH", json: { name: name.trim(), description, permissions: [...perms] } }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: ["roles"] });
      toast.success(isNew ? "Đã tạo vai trò." : "Đã lưu quyền của vai trò.");
      onClose();
    },
    onError: (e) => {
      setServerError(errorMessage(e));
      announce(errorMessage(e));
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (!name.trim()) {
      announce("Nhập tên vai trò.");
      return;
    }
    save.mutate();
  };

  return (
    <Dialog open={open} onClose={onClose} title={isNew ? "Tạo vai trò" : `Vai trò ${role?.name}`} size="lg"
      description={locked ? "Vai trò Quản trị luôn có toàn quyền để tránh mất quyền quản trị hệ thống." : undefined}>
      <form onSubmit={submit} noValidate className="space-y-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Tên vai trò" required error={!name.trim() ? "Nhập tên vai trò." : undefined} showError={submitted}>
            <Input value={name} onChange={(e) => setName(e.target.value)} disabled={locked} />
          </Field>
          <Field label="Mô tả">
            <Input value={description} onChange={(e) => setDescription(e.target.value)} disabled={locked} />
          </Field>
        </div>
        {groups.map((g) => (
          <fieldset key={g.module} className="rounded-sm border border-line">
            <legend className="ml-3 px-1 text-sm font-semibold">{g.label}</legend>
            <ul className="divide-y divide-line">
              {g.permissions.map((p) => (
                <li key={p.code}>
                  <label className="flex cursor-pointer items-center gap-2.5 px-3 py-1.5 hover:bg-surface-2">
                    <input
                      type="checkbox"
                      className="size-4 accent-[var(--primary)]"
                      checked={locked || perms.has(p.code)}
                      disabled={locked}
                      onChange={(e) => toggle(p.code, e.target.checked)}
                    />
                    <span className="flex-1">{p.description}</span>
                    <code className="hidden font-mono text-xs text-muted sm:inline">{p.code}</code>
                  </label>
                </li>
              ))}
            </ul>
          </fieldset>
        ))}
        {serverError && <p role="alert" className="rounded-sm bg-failed-soft px-2.5 py-2 text-sm text-failed">{serverError}</p>}
        <div className="flex justify-end gap-2 border-t border-line pt-3">
          <Button onClick={onClose}>{locked ? "Đóng" : "Huỷ"}</Button>
          {!locked && <Button type="submit" variant="primary" loading={save.isPending}>{isNew ? "Tạo vai trò" : "Lưu quyền"}</Button>}
        </div>
      </form>
    </Dialog>
  );
}
