import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronLeft, KeyRound, Pencil, PlugZap, ScrollText, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import type { ConnectionProfile, ConnectorType, Purpose, TestResult } from "@/core/api/types";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { ConfirmDangerDialog } from "@/core/ui/confirm-danger-dialog";
import { ErrorState, LoadingRows, Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { announce } from "@/core/ui/live-region";
import { formatDate, formatDateTime, pluralDays } from "@/core/lib/format";
import { connectionsKey, useConnection, useConnectorTypes } from "./api";
import { ConnectionFormDialog } from "./ConnectionFormDialog";
import { CredentialDialog } from "./CredentialDialog";
import { ProfileStatusBadge } from "./ConnectionStatus";
import { TestResultView } from "./TestResultView";

const PURPOSE_NAME: Record<Purpose, string> = { read: "Read", write: "Write" };

function CredentialPanel({ profile, type, purpose }: { profile: ConnectionProfile; type: ConnectorType; purpose: Purpose }) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const cred = profile.credentials[purpose];
  const [params, setParams] = useSearchParams();
  // Right after "Thêm kết nối" (?credential=read) the API key dialog opens by itself.
  const [editOpen, setEditOpen] = useState(() => params.get("credential") === purpose && !cred && can("core.connections.manage"));
  const [removeOpen, setRemoveOpen] = useState(false);
  const [result, setResult] = useState<TestResult | null>(cred?.last_test_result ?? null);
  const authLabel = type.auth_types.find((a) => a.key === cred?.auth_type)?.label ?? cred?.auth_type;

  const test = useMutation({
    mutationFn: () => api<TestResult>(`/api/core/connections/${profile.id}/test?purpose=${purpose}`, { method: "POST" }),
    onSuccess: async (r) => {
      setResult(r);
      await qc.invalidateQueries({ queryKey: connectionsKey });
      const msg = r.ok ? `Kết nối ${PURPOSE_NAME[purpose]} thành công.` : `Kết nối ${PURPOSE_NAME[purpose]} chưa thành công. Xem chi tiết từng bước.`;
      if (r.ok) toast.success(msg);
      else toast.error(msg);
      announce(msg);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const remove = useMutation({
    mutationFn: () => api(`/api/core/connections/${profile.id}/credentials/${purpose}`, { method: "DELETE" }),
    onSuccess: async () => {
      setRemoveOpen(false);
      setResult(null);
      await qc.invalidateQueries({ queryKey: connectionsKey });
      toast.success(`Đã xoá credential ${PURPOSE_NAME[purpose]}.`);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  return (
    <Panel
      title={`Credential ${PURPOSE_NAME[purpose]}`}
      description={purpose === "read" ? "Đọc dữ liệu cho đối soát và báo cáo." : "Ghi dữ liệu khi thực thi workflow."}
      actions={
        <>
          {cred && can("core.connections.test") && (
            <Button size="sm" onClick={() => test.mutate()} loading={test.isPending}>
              <PlugZap className="size-4" aria-hidden="true" />
              Kiểm tra kết nối
            </Button>
          )}
          {can("core.connections.manage") && (
            <Button size="sm" variant={cred ? "secondary" : "primary"} onClick={() => setEditOpen(true)}>
              <KeyRound className="size-4" aria-hidden="true" />
              {cred ? "Thay mới" : "Nhập credential"}
            </Button>
          )}
        </>
      }
    >
      {!cred ? (
        <p className="text-sm text-muted">
          Chưa cấu hình.{" "}
          {purpose === "write"
            ? "Các thao tác ghi (gán tài sản, tạo/vô hiệu hoá người dùng…) sẽ bị khoá cho tới khi có credential Write."
            : "Đối soát và báo cáo chưa lấy được dữ liệu từ hệ thống này."}
        </p>
      ) : (
        <>
          <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5 text-base">
            <dt className="text-muted">Phương thức</dt>
            <dd>{authLabel}</dd>
            {Object.entries(cred.public)
              .filter(([, v]) => v)
              .map(([k, v]) => (
                <div key={k} className="contents">
                  <dt className="text-muted">{type.auth_types.flatMap((a) => a.fields).find((f) => f.name === k)?.label ?? k}</dt>
                  <dd>
                    <Mono>{v}</Mono>
                  </dd>
                </div>
              ))}
            <dt className="text-muted">Secret</dt>
            <dd>
              <Mono>{cred.hint ? `••••${cred.hint}` : "••••••••"}</Mono>
            </dd>
            <dt className="text-muted">Hết hạn</dt>
            <dd className={cred.expiring ? "text-waiting" : undefined}>
              {cred.expires_at ? `${formatDate(cred.expires_at)} (${pluralDays(cred.days_left ?? 0)})` : "Không khai báo"}
            </dd>
            <dt className="text-muted">Thay mới lần cuối</dt>
            <dd>{formatDateTime(cred.rotated_at)}</dd>
          </dl>
          {result ? (
            <div className="mt-4 border-t border-line pt-4">
              <TestResultView result={result} />
            </div>
          ) : (
            <p className="mt-4 border-t border-line pt-3 text-sm text-muted">Chưa kiểm tra. Bấm “Kiểm tra kết nối” để xác nhận DNS, SSL, đăng nhập và quyền.</p>
          )}
          {can("core.connections.manage") && (
            <div className="mt-4 border-t border-line pt-3">
              <Button size="sm" variant="ghost" className="text-failed" onClick={() => setRemoveOpen(true)}>
                <Trash2 className="size-4" aria-hidden="true" />
                Xoá credential {PURPOSE_NAME[purpose]}
              </Button>
            </div>
          )}
        </>
      )}
      <CredentialDialog open={editOpen} onClose={() => { setEditOpen(false); if (params.has("credential")) setParams({}, { replace: true }); }} profile={profile} type={type} purpose={purpose} />
      <ConfirmDangerDialog
        open={removeOpen}
        onClose={() => setRemoveOpen(false)}
        onConfirm={() => remove.mutate()}
        loading={remove.isPending}
        title={`Xoá credential ${PURPOSE_NAME[purpose]}?`}
        confirmText={profile.name}
        confirmLabel="Xoá credential"
        consequences={
          <p>
            Secret sẽ bị xoá vĩnh viễn.{" "}
            {purpose === "write"
              ? "Mọi thao tác ghi tới hệ thống này sẽ bị khoá."
              : "Đối soát sẽ không đọc được dữ liệu từ hệ thống này."}
          </p>
        }
      />
    </Panel>
  );
}

export function ConnectionDetailPage() {
  const { id = "" } = useParams();
  const { can } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const q = useConnection(id);
  const types = useConnectorTypes();
  const [editOpen, setEditOpen] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);

  const del = useMutation({
    mutationFn: () => api(`/api/core/connections/${id}`, { method: "DELETE" }),
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: connectionsKey });
      toast.success("Đã xoá kết nối.");
      navigate("/settings/connections");
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const back = (
    <Link to="/settings/connections" className="mb-2 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
      <ChevronLeft className="size-4" aria-hidden="true" />
      Kết nối hệ thống
    </Link>
  );
  if (q.isLoading || types.isLoading) return <>{back}<LoadingRows rows={5} /></>;
  if (q.isError) return <>{back}<ErrorState error={q.error} onRetry={() => q.refetch()} /></>;
  const profile = q.data!;
  const type = types.data?.find((t) => t.system_type === profile.system_type);
  if (!type) return <>{back}<ErrorState error={new Error("Loại hệ thống không còn được hỗ trợ.")} /></>;

  return (
    <>
      <nav aria-label="Đường dẫn">{back}</nav>
      <PageHeader
        title={profile.name}
        description={
          <span className="inline-flex flex-wrap items-center gap-2">
            {profile.system_name} · {profile.environment === "prod" ? "Production" : "Test"} · <ProfileStatusBadge profile={profile} />
          </span>
        }
        actions={
          <>
            {can("core.audit.view") && (
              <Link
                to={`/settings/audit?target_id=${profile.id}`}
                className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-3 hover:bg-surface-2"
              >
                <ScrollText className="size-4" aria-hidden="true" />
                Lịch sử thay đổi
              </Link>
            )}
            {can("core.connections.manage") && (
              <Button onClick={() => setEditOpen(true)}>
                <Pencil className="size-4" aria-hidden="true" />
                Sửa
              </Button>
            )}
          </>
        }
      />
      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Panel title="Thông tin kết nối" className="xl:col-span-2">
          <dl className="grid gap-x-8 gap-y-2 text-base sm:grid-cols-[max-content_1fr_max-content_1fr]">
            <dt className="text-muted">Địa chỉ</dt>
            <dd>
              <Mono>{profile.base_url}</Mono>
            </dd>
            <dt className="text-muted">Trạng thái</dt>
            <dd>{profile.enabled ? "Đang bật" : "Đang tắt"}</dd>
            {Object.entries(type.config_schema.properties ?? {})
              .filter(([, prop]) => Object.entries(prop["x-show-when"] ?? {}).every(([c, v]) => profile.config[c] === v))
              .map(([k, prop]) => (
              <div key={k} className="contents">
                <dt className="text-muted">{prop.title ?? k}</dt>
                <dd>{typeof profile.config[k] === "boolean" ? (profile.config[k] ? "Có" : "Không") : String(profile.config[k] ?? "—")}</dd>
              </div>
            ))}
            <dt className="text-muted">Dùng bởi</dt>
            <dd>{profile.used_by.map((m) => m.name).join(", ") || "Chưa có module"}</dd>
            <dt className="text-muted">Cập nhật</dt>
            <dd>{formatDateTime(profile.updated_at)}</dd>
            {profile.notes && (
              <>
                <dt className="text-muted">Ghi chú</dt>
                <dd className="whitespace-pre-line sm:col-span-3">{profile.notes}</dd>
              </>
            )}
          </dl>
        </Panel>
        <CredentialPanel profile={profile} type={type} purpose="read" />
        <CredentialPanel profile={profile} type={type} purpose="write" />
        {can("core.connections.manage") && (
          <Panel title="Xoá kết nối" className="border-failed/30 xl:col-span-2">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <p className="text-sm text-muted">Xoá cấu hình và toàn bộ credential. Các module đang dùng sẽ mất kết nối tới hệ thống này.</p>
              <Button variant="danger" onClick={() => setDeleteOpen(true)}>
                <Trash2 className="size-4" aria-hidden="true" />
                Xoá kết nối
              </Button>
            </div>
          </Panel>
        )}
      </div>
      {types.data && <ConnectionFormDialog open={editOpen} onClose={() => setEditOpen(false)} types={types.data} profile={profile} />}
      <ConfirmDangerDialog
        open={deleteOpen}
        onClose={() => setDeleteOpen(false)}
        onConfirm={() => del.mutate()}
        loading={del.isPending}
        title={`Xoá kết nối ${profile.name}?`}
        confirmText={profile.name}
        confirmLabel="Xoá kết nối"
        consequences={
          <>
            <p>Cấu hình và credential Read/Write sẽ bị xoá vĩnh viễn, không khôi phục được.</p>
            {profile.used_by.length > 0 && <p>Module bị ảnh hưởng: {profile.used_by.map((m) => m.name).join(", ")}.</p>}
          </>
        }
      />
    </>
  );
}
