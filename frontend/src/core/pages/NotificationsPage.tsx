import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";

interface Pref {
  has_email: boolean;
  email_digest: boolean;
  enabled: boolean;
  times: string[];
}

interface Settings {
  enabled: boolean;
  times: string[];
  smtp_profile_id: string | null;
  teams_profile_id: string | null;
  max_per_run: number;
  app_base_url: string;
}

interface Profiles {
  smtp: { id: string; name: string }[];
  teams: { id: string; name: string }[];
}

const PREF = ["me", "notifications"] as const;
const SETTINGS = ["core", "notifications", "settings"] as const;
const MANAGE = "core.notifications.manage";

function MyPreference() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: PREF, queryFn: () => api<Pref>("/api/core/me/notifications") });
  const save = useMutation({
    mutationFn: (email_digest: boolean) => api<Pref>("/api/core/me/notifications", { method: "PUT", json: { email_digest } }),
    onSuccess: async () => {
      toast.success("Đã lưu lựa chọn thông báo.");
      await qc.invalidateQueries({ queryKey: PREF });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (q.isLoading) return <LoadingRows rows={2} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const p = q.data!;
  return (
    <Panel
      title={
        <span className="inline-flex items-center gap-1.5">
          Thư tóm tắt “Việc tiếp theo”
          <HelpTip title="Khi nào có thư">
            <span className="block">Hệ thống gửi thư vào các giờ: {p.times.join(", ") || "chưa cấu hình"} (giờ Việt Nam).</span>
            <span className="block">Chỉ gồm việc của riêng bạn, theo quyền của bạn. Việc đã nhắc rồi sẽ không nhắc lại, trừ khi nó trở nên khẩn hơn. Không có việc mới thì không có thư.</span>
          </HelpTip>
        </span>
      }
      className="max-w-2xl"
    >
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="size-4 accent-primary"
          checked={p.email_digest}
          disabled={save.isPending}
          onChange={(e) => save.mutate(e.target.checked)}
        />
        Gửi thư tóm tắt cho tôi
      </label>
      {!p.has_email && (
        <p className="mt-3 text-sm text-muted">Tài khoản của bạn chưa có địa chỉ e-mail nên sẽ không nhận được thư. Nhờ quản trị viên cập nhật e-mail.</p>
      )}
      {!p.enabled && <p className="mt-3 text-sm text-muted">Quản trị viên chưa bật gửi thông báo; lựa chọn của bạn sẽ có hiệu lực khi được bật.</p>}
    </Panel>
  );
}

function AdminSettings() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: SETTINGS, queryFn: () => api<Settings>("/api/core/notifications/settings") });
  const profiles = useQuery({ queryKey: [...SETTINGS, "profiles"], queryFn: () => api<Profiles>("/api/core/notifications/profiles") });
  const [form, setForm] = useState<Settings | null>(null);
  const [times, setTimes] = useState("");
  useEffect(() => {
    if (q.data) {
      setForm(q.data);
      setTimes(q.data.times.join(", "));
    }
  }, [q.data]);
  const save = useMutation({
    mutationFn: () =>
      api<Settings>("/api/core/notifications/settings", {
        method: "PUT",
        json: { ...form, times: times.split(/[,\s]+/).filter(Boolean) },
      }),
    onSuccess: async () => {
      toast.success("Đã lưu cấu hình thông báo.");
      await qc.invalidateQueries({ queryKey: SETTINGS });
      await qc.invalidateQueries({ queryKey: PREF });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  if (q.isLoading) return <LoadingRows rows={3} />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  if (!form) return null;
  const set = (patch: Partial<Settings>) => setForm({ ...form, ...patch });
  return (
    <Panel title="Cấu hình gửi thông báo (quản trị)" className="max-w-2xl">
      <form
        className="grid gap-4"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <label className="flex items-center gap-2 text-sm">
          <input type="checkbox" className="size-4 accent-primary" checked={form.enabled} onChange={(e) => set({ enabled: e.target.checked })} />
          Bật gửi thông báo (công tắc tổng; tắt thì không gửi gì)
        </label>
        <Field label="Giờ gửi (giờ Việt Nam)" hint="Dạng HH:MM, cách nhau bằng dấu phẩy. Ví dụ: 08:00, 13:30">
          <Input value={times} onChange={(e) => setTimes(e.target.value)} />
        </Field>
        <Field label="Kết nối gửi thư (SMTP)" hint="Tạo ở Kết nối hệ thống, loại “Máy chủ gửi thư (SMTP)”, credential Write.">
          <Select value={form.smtp_profile_id ?? ""} onChange={(e) => set({ smtp_profile_id: e.target.value || null })}>
            <option value="">— Chưa chọn —</option>
            {profiles.data?.smtp.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </Field>
        <Field label="Kênh Teams (tuỳ chọn)" hint="Chỉ nhận số liệu tổng hợp của cả nhóm, không có tên người hay nội dung việc.">
          <Select value={form.teams_profile_id ?? ""} onChange={(e) => set({ teams_profile_id: e.target.value || null })}>
            <option value="">— Không gửi lên Teams —</option>
            {profiles.data?.teams.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </Select>
        </Field>
        <Field label="Địa chỉ web của app" hint="Dùng làm đường dẫn trong thư, ví dụ https://infra-ops.congty.local">
          <Input value={form.app_base_url} onChange={(e) => set({ app_base_url: e.target.value })} />
        </Field>
        <Field label="Số thư tối đa mỗi lượt chạy">
          <Input type="number" min={1} max={2000} value={form.max_per_run} onChange={(e) => set({ max_per_run: Number(e.target.value) })} />
        </Field>
        <div>
          <Button type="submit" variant="primary" loading={save.isPending}>Lưu cấu hình</Button>
        </div>
      </form>
    </Panel>
  );
}

/** Cá nhân › Thông báo: personal opt-in; administrators also see the platform settings. */
export function NotificationsPage() {
  const { can } = useAuth();
  return (
    <>
      <PageHeader title="Thông báo" description="Nhận thư tóm tắt các việc cần làm theo giờ cố định, thay vì phải mở app." />
      <div className="grid gap-4">
        <MyPreference />
        {can(MANAGE) && <AdminSettings />}
      </div>
    </>
  );
}
