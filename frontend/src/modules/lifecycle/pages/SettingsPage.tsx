import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select, Switch } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";
import type { Kind } from "../api";
import { KIND_LABEL } from "../labels";

interface Values {
  sdp_profile_id: string | null;
  biostar_profile_id: string | null;
  rtr_requester_name: string;
  rtr_lookback_days: number;
  field_labels: Record<string, string>;
  change_type_map: Record<string, Kind>;
  sdp_user_employee_id_field: string;
  match_assets_by_name: boolean;
  asset_product_types: string[];
  asset_state_in_use: string;
  asset_state_in_store: string;
  onboard_grace_days: number;
  offboard_grace_days: number;
  snapshot_retention_days: number;
  reviewer_name: string;
}

interface SettingsResponse {
  values: Values;
  field_titles: Record<string, string>;
  default_labels: Record<string, string>;
  profiles: { id: string; name: string; system_type: string; environment: string; enabled: boolean }[];
}

export function SettingsPage() {
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["lifecycle", "settings"], queryFn: () => api<SettingsResponse>("/api/lifecycle/settings") });
  const types = useQuery({
    queryKey: ["lifecycle", "change-types"],
    queryFn: () => api<{ raw: string; count: number; kind: Kind | null; explicit: Kind | null }[]>("/api/lifecycle/change-types"),
  });
  const [v, setV] = useState<Values | null>(null);
  const [productTypes, setProductTypes] = useState("");
  const [serverError, setServerError] = useState("");
  useEffect(() => {
    if (q.data) {
      setV(q.data.values);
      setProductTypes(q.data.values.asset_product_types.join(", "));
    }
  }, [q.data]);

  const save = useMutation({
    mutationFn: (values: Values) => api<{ reparsed: number }>("/api/lifecycle/settings", { method: "PUT", json: values }),
    onSuccess: async (r) => {
      await qc.invalidateQueries({ queryKey: ["lifecycle"] });
      setServerError("");
      toast.success(`Đã lưu cấu hình và đọc lại ${r.reparsed} request. Bấm “Đồng bộ ngay” để cập nhật đối soát.`);
    },
    onError: (e) => {
      const msg =
        e instanceof ApiError && Array.isArray(e.details)
          ? `${e.message} ${(e.details as { loc: string[]; msg: string }[]).map((d) => `${d.loc.join(".")}: ${d.msg}`).join("; ")}`
          : errorMessage(e);
      setServerError(msg);
      announce(msg);
    },
  });

  if (q.isLoading || !v) return <>{q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : <LoadingRows rows={8} />}</>;
  const d = q.data!;
  const set = <K extends keyof Values>(key: K, value: Values[K]) => setV((cur) => (cur ? { ...cur, [key]: value } : cur));
  const num = (key: keyof Values) => (e: React.ChangeEvent<HTMLInputElement>) => set(key, Number(e.target.value) as never);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    save.mutate({ ...v, asset_product_types: productTypes.split(",").map((s) => s.trim()).filter(Boolean) });
  };
  const profileOptions = (type: string) => d.profiles.filter((p) => p.system_type === type);

  return (
    <>
      <PageHeader
        title="Cấu hình module Vòng đời nhân sự"
        description="Cách infra-ops đọc request RTR, nối tài sản với nhân viên và phát hiện lệch. Thay đổi được ghi vào Nhật ký kiểm tra."
      />
      <form onSubmit={submit} noValidate className="space-y-4">
        <Panel title="Kết nối sử dụng" description="Để trống để dùng kết nối Production đang bật đầu tiên của từng hệ thống.">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="ServiceDesk Plus">
              <Select value={v.sdp_profile_id ?? ""} onChange={(e) => set("sdp_profile_id", e.target.value || null)}>
                <option value="">Tự động</option>
                {profileOptions("sdp").map((p) => <option key={p.id} value={p.id}>{p.name}{p.enabled ? "" : " (đang tắt)"}</option>)}
              </Select>
            </Field>
            <Field label="BioStar">
              <Select value={v.biostar_profile_id ?? ""} onChange={(e) => set("biostar_profile_id", e.target.value || null)}>
                <option value="">Tự động</option>
                {profileOptions("biostar").map((p) => <option key={p.id} value={p.id}>{p.name}{p.enabled ? "" : " (đang tắt)"}</option>)}
              </Select>
            </Field>
          </div>
        </Panel>

        <Panel title="Request RTR trên ServiceDesk Plus">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Tên người yêu cầu (requester)" hint="Request do hệ thống RTR tạo có requester này.">
              <Input value={v.rtr_requester_name} onChange={(e) => set("rtr_requester_name", e.target.value)} />
            </Field>
            <Field label="Đọc request trong (ngày)" hint="Từ 7 đến 1095 ngày gần nhất.">
              <Input type="number" min={7} max={1095} value={v.rtr_lookback_days} onChange={num("rtr_lookback_days")} className="w-32" />
            </Field>
          </div>
          <h3 className="mt-5 mb-1 text-base font-semibold">Nhãn trong nội dung request</h3>
          <p className="mb-3 text-sm text-muted">Nội dung request có dạng “Nhãn: giá trị”. Đổi nhãn khi mẫu RTR thay đổi, không cần sửa code.</p>
          <div className="grid gap-x-4 gap-y-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(d.field_titles).map(([key, title]) => (
              <Field key={key} label={title}>
                <Input
                  value={v.field_labels[key] ?? d.default_labels[key] ?? ""}
                  onChange={(e) => set("field_labels", { ...v.field_labels, [key]: e.target.value })}
                />
              </Field>
            ))}
          </div>
        </Panel>

        <Panel title="Loại biến động" description="Gán mỗi giá trị “Loại biến động” xuất hiện trong request với cách infra-ops xử lý.">
          {types.data && types.data.length > 0 ? (
            <table className="w-full text-left">
              <caption className="sr-only">Mapping loại biến động</caption>
              <thead className="text-sm text-muted">
                <tr>
                  <th scope="col" className="py-1.5 font-semibold">Giá trị trong request</th>
                  <th scope="col" className="py-1.5 text-right font-semibold">Số request</th>
                  <th scope="col" className="py-1.5 pl-6 font-semibold">Xử lý là</th>
                </tr>
              </thead>
              <tbody>
                {types.data.map((t) => {
                  const current = v.change_type_map[t.raw] ?? "";
                  return (
                    <tr key={t.raw} className="border-t border-line">
                      <td className="py-1.5">{t.raw}</td>
                      <td className="py-1.5 text-right tabular">{t.count}</td>
                      <td className="py-1.5 pl-6">
                        <Select
                          aria-label={`Xử lý “${t.raw}” là`}
                          value={current}
                          onChange={(e) => {
                            const map = { ...v.change_type_map };
                            if (e.target.value) map[t.raw] = e.target.value as Kind;
                            else delete map[t.raw];
                            set("change_type_map", map);
                          }}
                          className="max-w-72"
                        >
                          <option value="">{t.kind && !t.explicit ? `Tự nhận diện: ${KIND_LABEL[t.kind]}` : "Chưa gán"}</option>
                          {Object.entries(KIND_LABEL).map(([k, label]) => <option key={k} value={k}>{label}</option>)}
                        </Select>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          ) : (
            <p className="text-sm text-muted">Chưa có giá trị nào. Danh sách xuất hiện sau lần đồng bộ đầu tiên.</p>
          )}
        </Panel>

        <Panel title="User và tài sản trên ServiceDesk Plus">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Trường Mã NV của user SDP" hint="Tên trường trong API, ví dụ employee_id hoặc udf_fields.udf_char1.">
              <Input value={v.sdp_user_employee_id_field} onChange={(e) => set("sdp_user_employee_id_field", e.target.value)} className="font-mono" />
            </Field>
            <Field label="Loại tài sản cần đối soát" hint="Tên product type trên SDP, cách nhau bằng dấu phẩy. Để trống để lấy tất cả.">
              <Input value={productTypes} onChange={(e) => setProductTypes(e.target.value)} />
            </Field>
            <Field label="Trạng thái “đang sử dụng”">
              <Input value={v.asset_state_in_use} onChange={(e) => set("asset_state_in_use", e.target.value)} />
            </Field>
            <Field label="Trạng thái “trong kho”">
              <Input value={v.asset_state_in_store} onChange={(e) => set("asset_state_in_store", e.target.value)} />
            </Field>
          </div>
          <div className="mt-4">
            <Switch
              label="Nối tài sản theo họ tên khi user SDP chưa có Mã NV"
              description="Chỉ dùng khi họ tên khớp duy nhất một user. Kết quả được ghi chú “nối theo họ tên”."
              checked={v.match_assets_by_name}
              onChange={(x) => set("match_assets_by_name", x)}
            />
          </div>
        </Panel>

        <Panel title="Ngưỡng đối soát và lưu trữ">
          <div className="grid gap-4 sm:grid-cols-3">
            <Field label="Onboard: cho phép trễ (ngày)" hint="Sau ngày hiệu lực bao lâu thì báo thiếu BioStar/tài sản.">
              <Input type="number" min={0} max={60} value={v.onboard_grace_days} onChange={num("onboard_grace_days")} className="w-28" />
            </Field>
            <Field label="Offboard: cho phép trễ (ngày)" hint="Sau ngày nghỉ việc bao lâu thì báo còn quyền/tài sản.">
              <Input type="number" min={0} max={60} value={v.offboard_grace_days} onChange={num("offboard_grace_days")} className="w-28" />
            </Field>
            <Field label="Giữ chi tiết mỗi lần đồng bộ (ngày)">
              <Input type="number" min={7} max={730} value={v.snapshot_retention_days} onChange={num("snapshot_retention_days")} className="w-28" />
            </Field>
          </div>
        </Panel>

        <Panel title="Biên bản" description="Dùng từ Phase 3 khi lập Biên bản Bàn giao / Thu hồi.">
          <Field label="Người kiểm tra (Nội dung B)" className="max-w-md">
            <Input value={v.reviewer_name} onChange={(e) => set("reviewer_name", e.target.value)} placeholder="Họ tên trưởng nhóm IT" />
          </Field>
        </Panel>

        {serverError && <p role="alert" className="rounded-sm bg-failed-soft px-3 py-2 text-sm text-failed">{serverError}</p>}
        <div className="sticky bottom-0 -mx-5 flex justify-end gap-2 border-t border-line bg-paper/95 px-5 py-3 backdrop-blur">
          <Button onClick={() => { setV(d.values); setProductTypes(d.values.asset_product_types.join(", ")); }}>Hoàn tác thay đổi</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>Lưu cấu hình</Button>
        </div>
      </form>
    </>
  );
}
