import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select, Textarea } from "@/core/ui/field";
import { StatusBadge } from "@/core/ui/status";
import { useTechnicians } from "../api";

interface Values {
  sdp_profile_id: string | null;
  lookback_days: number;
  group_names: string[];
  done_statuses: string[];
  cancelled_statuses: string[];
}
interface SettingsResponse {
  values: Values;
  profiles: { id: string; name: string; environment: string; enabled: boolean }[];
}

const lines = (v: string) => v.split("\n").map((x) => x.trim()).filter(Boolean);

function SettingsForm({ data }: { data: SettingsResponse }) {
  const qc = useQueryClient();
  const [v, setV] = useState(data.values);
  const [text, setText] = useState({
    group_names: data.values.group_names.join("\n"),
    done_statuses: data.values.done_statuses.join("\n"),
    cancelled_statuses: data.values.cancelled_statuses.join("\n"),
  });
  const lookbackBad = !(v.lookback_days >= 30 && v.lookback_days <= 1100);
  const save = useMutation({
    mutationFn: () =>
      api("/api/workload/settings", {
        method: "PUT",
        json: { ...v, group_names: lines(text.group_names), done_statuses: lines(text.done_statuses), cancelled_statuses: lines(text.cancelled_statuses) },
      }),
    onSuccess: () => {
      toast.success("Đã lưu cấu hình. Áp dụng từ lần đồng bộ tiếp theo.");
      void qc.invalidateQueries({ queryKey: ["workload"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Panel title="Đồng bộ từ ServiceDesk Plus" className="mb-4">
      <form
        className="grid max-w-3xl gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!lookbackBad) save.mutate();
        }}
      >
        <Field label="Kết nối SDP" hint="Để trống: dùng kết nối SDP Production đầu tiên đang bật.">
          <Select value={v.sdp_profile_id ?? ""} onChange={(e) => setV({ ...v, sdp_profile_id: e.target.value || null })}>
            <option value="">Tự động</option>
            {data.profiles.map((p) => <option key={p.id} value={p.id}>{p.name}{p.enabled ? "" : " (đang tắt)"}</option>)}
          </Select>
        </Field>
        <Field label="Đọc lần đầu (ngày)" hint="Từ 30 đến 1100 ngày. Nên ≥ 366 để có thống kê 1 năm." error="Nhập từ 30 đến 1100." showError={lookbackBad}>
          <Input type="number" min={30} max={1100} value={v.lookback_days} onChange={(e) => setV({ ...v, lookback_days: Number(e.target.value) })} />
        </Field>
        <Field label="Chỉ lấy nhóm hỗ trợ" hint="Mỗi dòng một nhóm (Group trên SDP). Để trống: lấy mọi nhóm.">
          <Textarea rows={3} value={text.group_names} onChange={(e) => setText({ ...text, group_names: e.target.value })} />
        </Field>
        <div />
        <Field label="Trạng thái được tính là đã thực hiện" hint="Mỗi dòng một trạng thái SDP, không phân biệt hoa thường.">
          <Textarea rows={3} value={text.done_statuses} onChange={(e) => setText({ ...text, done_statuses: e.target.value })} />
        </Field>
        <Field label="Trạng thái được tính là đã huỷ" hint="Không tính vào hoàn tất hay quá hạn.">
          <Textarea rows={3} value={text.cancelled_statuses} onChange={(e) => setText({ ...text, cancelled_statuses: e.target.value })} />
        </Field>
        <div className="sm:col-span-2">
          <Button type="submit" variant="primary" loading={save.isPending}>Lưu cấu hình</Button>
        </div>
      </form>
    </Panel>
  );
}

function TechnicianLinks() {
  const qc = useQueryClient();
  const techs = useTechnicians();
  const users = useQuery({ queryKey: ["workload", "users"], queryFn: () => api<{ id: string; upn: string; display_name: string }[]>("/api/workload/users") });
  const link = useMutation({
    mutationFn: ({ id, user_id }: { id: string; user_id: string }) =>
      user_id
        ? api(`/api/workload/technicians/${id}/user`, { method: "PUT", json: { user_id } })
        : api(`/api/workload/technicians/${id}/user`, { method: "DELETE" }),
    onSuccess: () => {
      toast.success("Đã cập nhật liên kết.");
      void qc.invalidateQueries({ queryKey: ["workload", "technicians"] });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Panel
      title="Operator ↔ Technician SDP"
      description="Technician được liên kết tự động khi e-mail trên SDP trùng tài khoản đăng nhập. Chọn tay khi khác nhau; chọn “Tự động” để quay lại khớp theo e-mail."
      bodyClassName="p-0"
    >
      {techs.isLoading && <LoadingRows rows={4} />}
      {techs.isError && <div className="p-4"><ErrorState error={techs.error} /></div>}
      {techs.data && (
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm">
            <caption className="sr-only">Liên kết Operator với technician SDP</caption>
            <thead className="bg-surface-2 text-muted">
              <tr>
                <th scope="col" className="px-3 py-2 font-semibold">Technician SDP</th>
                <th scope="col" className="px-3 py-2 font-semibold">E-mail</th>
                <th scope="col" className="px-3 py-2 font-semibold">Trạng thái</th>
                <th scope="col" className="px-3 py-2 font-semibold">Tài khoản infra-ops</th>
              </tr>
            </thead>
            <tbody>
              {techs.data.items.length === 0 && (
                <tr><td colSpan={4} className="px-3 py-4 text-muted">Chưa có technician. Chạy đồng bộ để lấy danh sách từ SDP.</td></tr>
              )}
              {techs.data.items.map((t) => (
                <tr key={t.id} className="border-t border-line">
                  <th scope="row" className="px-3 py-2 font-medium">{t.name}</th>
                  <td className="px-3 py-2 text-muted">{t.email ?? "—"}</td>
                  <td className="px-3 py-2">{t.active ? <StatusBadge status="done" label="Còn trên SDP" /> : <StatusBadge status="disabled" label="Không còn trên SDP" />}</td>
                  <td className="px-3 py-1.5">
                    <Select
                      aria-label={`Tài khoản liên kết với ${t.name}`}
                      value={t.user?.id ?? ""}
                      disabled={link.isPending}
                      onChange={(e) => link.mutate({ id: t.id, user_id: e.target.value })}
                    >
                      <option value="">{t.mapped_manually ? "Tự động (theo e-mail)" : t.user ? "Tự động" : "Chưa liên kết – tự động"}</option>
                      {users.data?.map((u) => <option key={u.id} value={u.id}>{u.display_name} ({u.upn})</option>)}
                    </Select>
                    {t.user && <span className="mt-0.5 block text-xs text-muted">{t.mapped_manually ? "Chọn tay" : "Khớp tự động theo e-mail"}</span>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

export function SettingsPage() {
  const q = useQuery({ queryKey: ["workload", "settings"], queryFn: () => api<SettingsResponse>("/api/workload/settings") });
  return (
    <>
      <PageHeader title="Cấu hình công việc" description="Nguồn dữ liệu task từ ServiceDesk Plus và liên kết Operator." />
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading && <LoadingRows rows={5} />}
      {q.data && <SettingsForm data={q.data} />}
      <TechnicianLinks />
    </>
  );
}
