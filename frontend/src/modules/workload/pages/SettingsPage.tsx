import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select, Switch, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import { formatNumber, formatVnd } from "@/core/lib/format";
import { useTechnicians } from "../api";

interface Values {
  sdp_profile_id: string | null;
  lookback_days: number;
  group_names: string[];
  done_statuses: string[];
  cancelled_statuses: string[];
  in_progress_statuses: string[];
  on_hold_statuses: string[];
  auto_create_users: boolean;
  device_keywords: Record<"replace" | "upgrade" | "repair", string[]>;
  po_limit_vnd: number;
  quote_categories: string[];
  quote_threshold_vnd: number;
  quotes_required: number;
  asset_tag_prefix: string;
  asset_tag_codes: Record<string, string>;
  po_email_to: string[];
  po_email_cc: string[];
  po_email_subject: string;
  po_email_body: string;
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
    in_progress_statuses: data.values.in_progress_statuses.join("\n"),
    on_hold_statuses: data.values.on_hold_statuses.join("\n"),
    kw_replace: data.values.device_keywords.replace.join("\n"),
    kw_upgrade: data.values.device_keywords.upgrade.join("\n"),
    kw_repair: data.values.device_keywords.repair.join("\n"),
    po_email_to: data.values.po_email_to.join(", "),
    quote_categories: data.values.quote_categories.join(", "),
    po_email_cc: data.values.po_email_cc.join(", "),
    asset_tag_codes: Object.entries(data.values.asset_tag_codes).map(([k, c]) => `${k}=${c}`).join(", "),
  });
  const lookbackBad = !(v.lookback_days >= 30 && v.lookback_days <= 1100);
  const limitBad = !(v.po_limit_vnd >= 1_000_000);
  const prefixBad = !v.asset_tag_prefix.trim();
  const emails = (t: string) => t.split(/[,;\s]+/).map((x) => x.trim()).filter(Boolean);
  const save = useMutation({
    mutationFn: () =>
      api("/api/workload/settings", {
        method: "PUT",
        json: {
          ...v,
          group_names: lines(text.group_names),
          done_statuses: lines(text.done_statuses),
          cancelled_statuses: lines(text.cancelled_statuses),
          in_progress_statuses: lines(text.in_progress_statuses),
          on_hold_statuses: lines(text.on_hold_statuses),
          device_keywords: { replace: lines(text.kw_replace), upgrade: lines(text.kw_upgrade), repair: lines(text.kw_repair) },
          po_email_to: emails(text.po_email_to),
          quote_categories: text.quote_categories.split(",").map((x) => x.trim()).filter(Boolean),
          po_email_cc: emails(text.po_email_cc),
          asset_tag_prefix: v.asset_tag_prefix.trim(),
          // "Laptop=LT, Màn hình=MN" → { Laptop: "LT", "Màn hình": "MN" }
          asset_tag_codes: Object.fromEntries(
            text.asset_tag_codes.split(",").map((x) => x.split("=").map((y) => y.trim())).filter(([k, c]) => k && c),
          ),
        },
      }),
    onSuccess: () => {
      toast.success("Đã lưu cấu hình. Áp dụng từ lần đồng bộ tiếp theo.");
      void qc.invalidateQueries({ queryKey: ["workload"] });
    },
  });
  return (
    <Panel title="Đồng bộ từ ServiceDesk Plus" className="mb-4">
      <form
        className="grid max-w-3xl gap-4 sm:grid-cols-2"
        onSubmit={(e) => {
          e.preventDefault();
          if (!lookbackBad && !limitBad && !prefixBad) save.mutate();
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
        <Field label="Trạng thái được tính là đang thực hiện" hint="Task đang mở có trạng thái SDP này hiện ở mục “Đang thực hiện”.">
          <Textarea rows={2} value={text.in_progress_statuses} onChange={(e) => setText({ ...text, in_progress_statuses: e.target.value })} />
        </Field>
        <Field label="Trạng thái được tính là tạm dừng" hint="Các trạng thái mở khác được tính là “Chưa bắt đầu”.">
          <Textarea rows={2} value={text.on_hold_statuses} onChange={(e) => setText({ ...text, on_hold_statuses: e.target.value })} />
        </Field>
        <div className="sm:col-span-2">
          <Switch
            label="Tự tạo tài khoản Operator cho technician chưa có tài khoản"
            description="Mỗi lần đồng bộ: tạo tài khoản theo e-mail của technician (đăng nhập bằng SSO), vai trò Operator, liên kết luôn."
            checked={v.auto_create_users}
            onChange={(c) => setV({ ...v, auto_create_users: c })}
          />
        </div>
        <fieldset className="grid gap-3 sm:col-span-2 sm:grid-cols-3">
          <legend className="mb-1 text-sm font-medium">Từ khoá nhận diện yêu cầu thiết bị (gợi ý đề xuất mua)</legend>
          <Field label="Thay thế"><Textarea rows={3} value={text.kw_replace} onChange={(e) => setText({ ...text, kw_replace: e.target.value })} /></Field>
          <Field label="Nâng cấp"><Textarea rows={3} value={text.kw_upgrade} onChange={(e) => setText({ ...text, kw_upgrade: e.target.value })} /></Field>
          <Field label="Sửa chữa"><Textarea rows={3} value={text.kw_repair} onChange={(e) => setText({ ...text, kw_repair: e.target.value })} /></Field>
        </fieldset>
        <fieldset className="grid gap-3 border-t border-line pt-4 sm:col-span-2 sm:grid-cols-2">
          <legend className="flex items-center gap-1 pt-2 text-base font-semibold">
            PO tổng và email gửi Phòng Mua hàng
            <HelpTip title="Mẫu email khi chốt PO">
              <span className="block">Khi Supervisor bấm <b>Chốt PO</b>, hệ thống tạo file .eml (mở bằng Outlook là email nháp) trình phê duyệt PO theo mẫu dưới.</span>
              <span className="block">Biến dùng được: <b>{"{{ po_number }}"}</b> số PO, <b>{"{{ date }}"}</b> ngày chốt, <b>{"{{ total }}"}</b> tổng tiền, <b>{"{{ limit }}"}</b> hạn mức, <b>{"{{ item_count }}"}</b> số hạng mục, <b>{"{{ items_table }}"}</b> bảng hạng mục kèm các báo giá so sánh (giá được chọn, nhà cung cấp không có hàng), <b>{"{{ closed_by }}"}</b> người chốt.</span>
              <span className="block">Email luôn kèm file Excel chi tiết, file báo giá, ảnh chụp không có hàng và bằng chứng Function Head. Mẫu cũ dùng {"{{ items_text }}"} vẫn hiện bảng trong bản HTML.</span>
            </HelpTip>
          </legend>
          <Field
            label="Hạn mức mỗi PO (đ)"
            hint={`${formatVnd(v.po_limit_vnd)} – vượt mức này hệ thống tự mở PO tiếp theo.`}
            error="Tối thiểu 1.000.000 đ."
            showError={limitBad}
          >
            <Input
              inputMode="numeric"
              value={v.po_limit_vnd ? formatNumber(v.po_limit_vnd) : ""}
              onChange={(e) => setV({ ...v, po_limit_vnd: Number(e.target.value.replace(/\D/g, "").slice(0, 12)) || 0 })}
            />
          </Field>
          <div />
          <Field label="Nhóm thiết bị định giá theo báo giá" hint="Nhóm trong Bảng giá, cách nhau dấu phẩy (vd. PC, Laptop). Các nhóm khác dùng giá trong Bảng giá.">
            <Input value={text.quote_categories} onChange={(e) => setText({ ...text, quote_categories: e.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Ngưỡng cần nhiều báo giá (đ)" hint={formatVnd(v.quote_threshold_vnd)}>
              <Input
                inputMode="numeric"
                value={formatNumber(v.quote_threshold_vnd)}
                onChange={(e) => setV({ ...v, quote_threshold_vnd: Number(e.target.value.replace(/\D/g, "").slice(0, 12)) || 0 })}
              />
            </Field>
            <Field label="Số báo giá bắt buộc" hint="Khi giá thấp nhất vượt ngưỡng.">
              <Input type="number" min={1} max={10} value={v.quotes_required} onChange={(e) => setV({ ...v, quotes_required: Number(e.target.value) })} />
            </Field>
          </div>
          <Field label="Tiền tố mã tài sản" hint={`Mã gợi ý khi nhận hàng: ${v.asset_tag_prefix || "…"}-<năm>-<loại>-<4 ký tự cuối serial>.`} error="Nhập tiền tố, vd. HCM." showError={prefixBad}>
            <Input value={v.asset_tag_prefix} maxLength={20} onChange={(e) => setV({ ...v, asset_tag_prefix: e.target.value })} />
          </Field>
          <Field label="Mã loại thiết bị" hint="Nhóm trong Bảng giá = mã, cách nhau dấu phẩy (vd. Laptop=LT, Màn hình=MN). Nhóm không có ở đây lấy chữ cái đầu.">
            <Input value={text.asset_tag_codes} onChange={(e) => setText({ ...text, asset_tag_codes: e.target.value })} />
          </Field>
          <Field label="Gửi tới" hint="Người phê duyệt PO: Head of IT, Phòng Finance (mua hàng). Các e-mail cách nhau bằng dấu phẩy.">
            <Input value={text.po_email_to} onChange={(e) => setText({ ...text, po_email_to: e.target.value })} placeholder="muahang@congty.vn" />
          </Field>
          <Field label="CC">
            <Input value={text.po_email_cc} onChange={(e) => setText({ ...text, po_email_cc: e.target.value })} />
          </Field>
          <Field label="Tiêu đề email" className="sm:col-span-2">
            <Input value={v.po_email_subject} maxLength={300} onChange={(e) => setV({ ...v, po_email_subject: e.target.value })} />
          </Field>
          <Field label="Nội dung email" className="sm:col-span-2">
            <Textarea rows={10} className="font-mono text-[0.8125rem]" value={v.po_email_body} maxLength={10000} onChange={(e) => setV({ ...v, po_email_body: e.target.value })} />
          </Field>
        </fieldset>
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
