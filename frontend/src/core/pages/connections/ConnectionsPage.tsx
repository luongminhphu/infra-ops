import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, Plug, Plus, Upload } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import type { ConnectionProfile } from "@/core/api/types";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { iconFor } from "@/core/ui/icons";
import { formatRelative } from "@/core/lib/format";
import { cn } from "@/core/lib/cn";
import { connectionsKey, useConnections, useConnectorTypes } from "./api";
import { ConnectionFormDialog } from "./ConnectionFormDialog";
import { CredentialLine, ProfileStatusBadge, STATUS_BAR } from "./ConnectionStatus";

function lastTested(p: ConnectionProfile): string | null {
  const times = [p.credentials.read?.last_test_at, p.credentials.write?.last_test_at].filter(Boolean) as string[];
  return times.sort().at(-1) ?? null;
}

function ConnectionRow({ profile, icon }: { profile: ConnectionProfile; icon: string }) {
  const Icon = iconFor(icon);
  return (
    <li className="relative flex flex-col gap-2 border-b border-line py-3 pr-4 pl-5 last:border-b-0 md:flex-row md:items-center md:gap-4">
      <span aria-hidden="true" className={cn("absolute top-3 bottom-3 left-0 w-1 rounded-r-xs", STATUS_BAR[profile.status])} />
      <div className="flex min-w-0 flex-1 items-start gap-3">
        <span className="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-sm bg-surface-2 text-muted">
          <Icon className="size-4" aria-hidden="true" />
        </span>
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/settings/connections/${profile.id}`} className="font-semibold hover:text-primary hover:underline">
              {profile.name}
            </Link>
            <span className="rounded-xs border border-line px-1.5 text-xs text-muted">{profile.environment === "prod" ? "Production" : "Test"}</span>
          </div>
          <p className="truncate text-sm text-muted">
            {profile.system_name} · <Mono>{profile.base_url}</Mono>
          </p>
          <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
            <CredentialLine purpose="read" cred={profile.credentials.read} />
            <CredentialLine purpose="write" cred={profile.credentials.write} />
          </div>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-3 pl-11 md:pl-0">
        <div className="text-sm text-muted md:w-44 md:text-right">
          <p>Dùng bởi: {profile.used_by.map((m) => m.name).join(", ") || "chưa có module"}</p>
          <p>Kiểm tra: {formatRelative(lastTested(profile))}</p>
        </div>
        <ProfileStatusBadge profile={profile} />
      </div>
    </li>
  );
}

export function ConnectionsPage() {
  const { can } = useAuth();
  const qc = useQueryClient();
  const types = useConnectorTypes();
  const list = useConnections();
  const [formOpen, setFormOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const canManage = can("core.connections.manage");

  const exportCfg = async () => {
    try {
      const data = await api<unknown>("/api/core/connections/export");
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `infra-ops-connections-${new Date().toISOString().slice(0, 10)}.json`;
      a.click();
      URL.revokeObjectURL(a.href);
      toast.success("Đã xuất cấu hình (không kèm secret).");
    } catch (e) {
      toast.error(errorMessage(e));
    }
  };

  const importCfg = useMutation({
    mutationFn: async (file: File) => api<{ created: string[]; updated: string[]; errors: { name: string; error: string }[]; credentials_cleared: string[] }>(
      "/api/core/connections/import",
      { method: "POST", json: JSON.parse(await file.text()) },
    ),
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: connectionsKey });
      const cleared = r.credentials_cleared.length
        ? ` Đã xoá credential do đổi địa chỉ máy chủ, cần nhập lại: ${r.credentials_cleared.join(", ")}.`
        : "";
      const msg = `Đã nhập: ${r.created.length} mới, ${r.updated.length} cập nhật.${cleared}`;
      if (r.errors.length) toast.warning(`${msg} ${r.errors.length} kết nối lỗi: ${r.errors.map((e) => `${e.name} (${e.error})`).join("; ")}`);
      else toast.success(`${msg} Nhập lại secret cho các kết nối mới.`);
    },
    onError: (e) => toast.error(e instanceof SyntaxError ? "File không phải JSON hợp lệ." : errorMessage(e)),
  });

  const iconOf = (t: string) => types.data?.find((x) => x.system_type === t)?.icon ?? "plug";

  return (
    <>
      <PageHeader
        title="Kết nối hệ thống"
        description="Địa chỉ, tài khoản và API key để infra-ops kết nối tới các hệ thống. Mỗi kết nối có credential Read (đọc, đối soát) và Write (thực thi workflow) riêng."
        actions={
          canManage && (
            <>
              <Button onClick={exportCfg}>
                <Download className="size-4" aria-hidden="true" />
                Xuất cấu hình
              </Button>
              <Button onClick={() => fileRef.current?.click()} loading={importCfg.isPending}>
                <Upload className="size-4" aria-hidden="true" />
                Nhập cấu hình
              </Button>
              <input
                ref={fileRef}
                type="file"
                accept="application/json,.json"
                className="sr-only"
                tabIndex={-1}
                aria-hidden="true"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) importCfg.mutate(f);
                  e.target.value = "";
                }}
              />
              <Button variant="primary" onClick={() => setFormOpen(true)} disabled={!types.data}>
                <Plus className="size-4" aria-hidden="true" />
                Thêm kết nối
              </Button>
            </>
          )
        }
      />
      {list.isError && <ErrorState error={list.error} onRetry={() => list.refetch()} />}
      <div className="rounded-md border border-line bg-surface">
        {list.isLoading && <LoadingRows rows={4} label="Đang tải danh sách kết nối" />}
        {list.data && list.data.length === 0 && (
          <EmptyState
            icon={Plug}
            title="Chưa có kết nối nào"
            action={
              canManage && (
                <Button variant="primary" onClick={() => setFormOpen(true)}>
                  <Plus className="size-4" aria-hidden="true" />
                  Thêm kết nối đầu tiên
                </Button>
              )
            }
          >
            Thêm kết nối tới ServiceDesk Plus và BioStar 2 để module Vòng đời nhân sự lấy được dữ liệu.
          </EmptyState>
        )}
        {list.data && list.data.length > 0 && (
          <ul aria-label="Danh sách kết nối">
            {list.data.map((p) => (
              <ConnectionRow key={p.id} profile={p} icon={iconOf(p.system_type)} />
            ))}
          </ul>
        )}
      </div>
      {types.data && <ConnectionFormDialog open={formOpen} onClose={() => setFormOpen(false)} types={types.data} />}
    </>
  );
}
