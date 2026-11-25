import { useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, LockKeyhole, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/core/api/client";
import type { ConnectorType, TestResult } from "@/core/api/types";
import { formatDateTime } from "@/core/lib/format";
import { Button } from "@/core/ui/button";
import { EmptyState, ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import { TestResultView } from "./connections/TestResultView";

interface MyCredential {
  profile_id: string;
  name: string;
  system_name: string;
  base_url: string;
  type: ConnectorType;
  credential: { auth_type: string; public: Record<string, string>; hint: string; updated_at: string; last_test_at: string | null; last_test_ok: boolean | null } | null;
}

const KEY = ["me", "credentials"] as const;

function CredentialCard({ c }: { c: MyCredential }) {
  const qc = useQueryClient();
  const cred = c.credential;
  const [editing, setEditing] = useState(!cred);
  const [authType, setAuthType] = useState(cred?.auth_type ?? c.type.auth_types[0]?.key ?? "");
  const [values, setValues] = useState<Record<string, string>>({ ...(cred?.public ?? {}) });
  const [submitted, setSubmitted] = useState(false);
  const [result, setResult] = useState<TestResult | null>(null);
  const spec = c.type.auth_types.find((a) => a.key === authType);
  const keep = Boolean(cred && cred.auth_type === authType);
  const missing = (spec?.fields ?? []).filter((f) => f.required && !values[f.name] && !(f.secret && keep));
  const refresh = () => qc.invalidateQueries({ queryKey: KEY });

  const save = useMutation({
    mutationFn: () => {
      const pub: Record<string, string> = {};
      const sec: Record<string, string> = {};
      for (const f of spec?.fields ?? []) (f.secret ? sec : pub)[f.name] = values[f.name] ?? "";
      return api(`/api/core/me/credentials/${c.profile_id}`, { method: "PUT", json: { auth_type: authType, public: pub, secrets: sec } });
    },
    onSuccess: async () => {
      setEditing(false);
      setValues((v) => Object.fromEntries(Object.entries(v).filter(([k]) => !spec?.fields.find((f) => f.name === k)?.secret)));
      await refresh();
      toast.success("Đã lưu API key. Bấm “Kiểm tra” để xác nhận key dùng được.");
    },
  });
  const test = useMutation({
    mutationFn: () => api<TestResult>(`/api/core/me/credentials/${c.profile_id}/test`, { method: "POST" }),
    onSuccess: async (r) => {
      setResult(r);
      await refresh();
    },
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/core/me/credentials/${c.profile_id}`, { method: "DELETE" }),
    onSuccess: async () => {
      setResult(null);
      setEditing(true);
      await refresh();
      toast.success("Đã xoá API key của bạn.");
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (!missing.length) save.mutate();
  };

  return (
    <Panel
      title={c.name}
      description={`${c.system_name} · ${c.base_url}`}
      actions={
        cred ? (
          <StatusBadge
            status={cred.last_test_ok === false ? "failed" : cred.last_test_ok ? "done" : "pending"}
            label={cred.last_test_ok === false ? "Key lỗi" : cred.last_test_ok ? "Đã kiểm tra" : "Chưa kiểm tra"}
          />
        ) : (
          <StatusBadge status="waiting" label="Chưa nhập key" />
        )
      }
    >
      {cred && !editing && (
        <div className="space-y-3">
          <p className="text-sm">
            Key <span className="font-mono">••••{cred.hint}</span> · cập nhật {formatDateTime(cred.updated_at)}
            {cred.last_test_at && <> · kiểm tra {formatDateTime(cred.last_test_at)}</>}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button variant="primary" onClick={() => test.mutate()} loading={test.isPending}>Kiểm tra</Button>
            <Button onClick={() => setEditing(true)}>
              <KeyRound className="size-4" aria-hidden="true" />Thay key
            </Button>
            <Button variant="ghost" onClick={() => remove.mutate()} loading={remove.isPending}>
              <Trash2 className="size-4" aria-hidden="true" />Xoá key
            </Button>
          </div>
          {result && <TestResultView result={result} />}
        </div>
      )}
      {editing && (
        <form onSubmit={submit} noValidate className="space-y-3">
          {c.type.auth_types.length > 1 && (
            <Field label="Phương thức xác thực">
              <Select value={authType} onChange={(e) => setAuthType(e.target.value)}>
                {c.type.auth_types.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
              </Select>
            </Field>
          )}
          {spec?.fields.map((f) => (
            <Field
              key={`${authType}-${f.name}`}
              label={f.label}
              required={f.required && !(f.secret && keep)}
              hint={f.help || (f.name === "api_key" ? "Trên ServiceDesk Plus: ảnh đại diện → Hồ sơ cá nhân → Tạo API key (technician của bạn)." : undefined)}
              error={missing.some((m) => m.name === f.name) ? `Nhập ${f.label.toLowerCase()}.` : undefined}
              showError={submitted}
            >
              <Input
                type={f.secret ? "password" : "text"}
                value={values[f.name] ?? ""}
                onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
                autoComplete={f.secret ? "new-password" : "off"}
                spellCheck={false}
                placeholder={f.secret && keep ? `Để trống để giữ key hiện tại (••••${cred?.hint})` : undefined}
              />
            </Field>
          ))}
          <p className="flex items-start gap-2 rounded-sm bg-surface-2 px-2.5 py-2 text-sm text-muted">
            <LockKeyhole className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
            Key được mã hoá khi lưu, không hiển thị lại cho ai (kể cả quản trị) và không ghi vào nhật ký.
          </p>
          <div className="flex gap-2">
            <Button type="submit" variant="primary" loading={save.isPending}>Lưu key</Button>
            {cred && <Button onClick={() => setEditing(false)}>Huỷ</Button>}
          </div>
        </form>
      )}
    </Panel>
  );
}

/** "Khoá API của tôi": the person's own keys, used for the work they are responsible for. */
export function MyCredentialsPage() {
  const q = useQuery({ queryKey: KEY, queryFn: () => api<MyCredential[]>("/api/core/me/credentials") });
  return (
    <>
      <PageHeader
        title="Khoá API của tôi"
        description={
          <>
            Job onboard/offboard bạn phụ trách, nhận hàng và chuyển việc đọc/ghi ServiceDesk Plus bằng key của chính bạn, nên SDP ghi nhận đúng người thực hiện.{" "}
            <HelpTip title="Khoá API của tôi">
              <span className="block">Chưa có key thì các bước đó dừng ở trạng thái chờ và tự chạy lại sau khi bạn lưu key.</span>
              <span className="block">Đồng bộ dữ liệu nền vẫn dùng tài khoản đọc chung do quản trị cấu hình.</span>
            </HelpTip>
          </>
        }
      />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading ? (
        <LoadingRows rows={3} />
      ) : q.data?.length ? (
        <div className="grid gap-4 xl:grid-cols-2">{q.data.map((c) => <CredentialCard key={c.profile_id} c={c} />)}</div>
      ) : (
        q.data && <Panel><EmptyState icon={KeyRound} title="Chưa có kết nối cần key cá nhân">Khi quản trị thêm kết nối ServiceDesk Plus, bạn nhập key của mình ở đây.</EmptyState></Panel>
      )}
    </>
  );
}
