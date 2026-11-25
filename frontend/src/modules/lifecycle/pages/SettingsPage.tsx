import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, ApiError, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select, Switch } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { Plus, Trash2 } from "lucide-react";
import { DateInput } from "@/core/ui/date-input";
import { announce } from "@/core/ui/live-region";
import type { Kind } from "../api";
import { KIND_LABEL } from "../labels";

interface Values {
  sdp_profile_id: string | null;
  biostar_profile_id: string | null;
  biostar_sources: BioStarSource[];
  biostar_retired_groups: string[];
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
  auto_create_jobs: boolean;
  sdp_close_status: string;
  sdp_closed_statuses: string[];
  sdp_add_note: boolean;
  biostar_user_group_id: string;
  biostar_access_group_ids: string[];
  biostar_expiry_date: string;
  biostar_offboard_action: "disable" | "delete";
  sdp_attach_path: string;
  sdp_attach_field: string;
  allocation_standard: StandardItem[];
  allocation_past_days: number;
}

interface BioStarSource {
  profile_id: string;
  id_prefix: string;
  id_prefix_to: string;
  id_keep_last: number;
}

/** What a BioStar user ID becomes after the prefix rule (mirrors backend normalize.map_employee_id). */
const mapId = (id: string, src: BioStarSource) => {
  const mapped = src.id_prefix && id.startsWith(src.id_prefix) ? src.id_prefix_to + id.slice(src.id_prefix.length) : id;
  return src.id_keep_last && mapped.length > src.id_keep_last ? mapped.slice(-src.id_keep_last) : mapped;
};

interface StandardItem {
  label: string;
  bands: string[];
  product_types: string[];
  product_contains: string;
  quantity: number;
  catalog_name: string;
}
type StdRow = Omit<StandardItem, "bands" | "product_types"> & { bands: string; product_types: string };
const toRows = (items: StandardItem[]): StdRow[] => items.map((i) => ({ ...i, bands: i.bands.join(", "), product_types: i.product_types.join(", ") }));

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
  const [closedStatuses, setClosedStatuses] = useState("");
  const [retiredGroups, setRetiredGroups] = useState("");
  const [std, setStd] = useState<StdRow[]>([]);
  const [loadGroups, setLoadGroups] = useState(false);
  const groups = useQuery({
    queryKey: ["lifecycle", "biostar-groups"],
    queryFn: () => api<{ user_groups: { id: string; name: string }[]; access_groups: { id: string; name: string }[] }>("/api/lifecycle/biostar/groups"),
    enabled: loadGroups,
    retry: false,
  });
  useEffect(() => {
    if (q.data) {
      setV(q.data.values);
      setProductTypes(q.data.values.asset_product_types.join(", "));
      setClosedStatuses(q.data.values.sdp_closed_statuses.join(", "));
      setRetiredGroups(q.data.values.biostar_retired_groups.join(", "));
      setStd(toRows(q.data.values.allocation_standard));
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
    const list = (text: string) => text.split(",").map((s) => s.trim()).filter(Boolean);
    save.mutate({
      ...v,
      asset_product_types: list(productTypes),
      sdp_closed_statuses: list(closedStatuses),
      biostar_retired_groups: list(retiredGroups),
      allocation_standard: std
        .filter((r) => r.label.trim())
        .map((r) => ({ ...r, label: r.label.trim(), bands: list(r.bands), product_types: list(r.product_types), quantity: Math.max(1, r.quantity || 1) })),
    });
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
            <Field
              label="BioStar"
              hint={
                !v.biostar_profile_id && profileOptions("biostar").length > 1
                  ? "Có nhiều kết nối BioStar: nên chọn đích danh hệ thống chính (Job onboard/offboard tạo và khoá user ở đây)."
                  : "Hệ thống chính: Job onboard/offboard tạo và khoá user ở đây."
              }
            >
              <Select value={v.biostar_profile_id ?? ""} onChange={(e) => set("biostar_profile_id", e.target.value || null)}>
                <option value="">Tự động</option>
                {profileOptions("biostar").map((p) => <option key={p.id} value={p.id}>{p.name}{p.enabled ? "" : " (đang tắt)"}</option>)}
              </Select>
            </Field>
          </div>
          <h3 className="mt-5 mb-1 flex items-center gap-1 text-base font-semibold">
            BioStar đọc thêm (chỉ để đối soát)
            <HelpTip title="BioStar đọc thêm">
              <span className="block">Hệ thống BioStar khác (vd. máy cũ ở văn phòng) được đọc mỗi lần đồng bộ và gộp với hệ thống chính theo Mã NV. Job không ghi vào các hệ thống này.</span>
              <span className="block">Nếu mã user trên hệ thống đó khác tiền tố so với Mã NV thật (vd. 100002 thay cho 000002), nhập Tiền tố = 1, Thay bằng = 0.</span>
              <span className="block">Một người có user ở cả hai hệ thống: user đang hoạt động được ưu tiên để không bỏ sót người đã nghỉ còn quyền ra vào.</span>
            </HelpTip>
          </h3>
          <p className="mb-3 text-sm text-muted">Mã NV và họ tên theo SDP: user có mã SDP không biết được tự khớp vào Mã NV SDP cùng số cuối và cùng họ tên. Quy tắc bên dưới chỉ cần khi tự khớp không đủ.</p>
          <ul className="space-y-3">
            {v.biostar_sources.map((src, k) => {
              const upd = (patch: Partial<BioStarSource>) => set("biostar_sources", v.biostar_sources.map((x, j) => (j === k ? { ...x, ...patch } : x)));
              return (
                <li key={k} className="grid items-end gap-3 rounded-sm border border-line p-3 sm:grid-cols-[1fr_8rem_8rem_8rem_auto]">
                  <Field label="Kết nối BioStar">
                    <Select value={src.profile_id} onChange={(e) => upd({ profile_id: e.target.value })}>
                      {profileOptions("biostar").map((p) => <option key={p.id} value={p.id}>{p.name}{p.enabled ? "" : " (đang tắt)"}</option>)}
                    </Select>
                  </Field>
                  <Field label="Tiền tố trên BioStar">
                    <Input value={src.id_prefix} maxLength={8} onChange={(e) => upd({ id_prefix: e.target.value.trim() })} placeholder="1" />
                  </Field>
                  <Field label="Thay bằng">
                    <Input value={src.id_prefix_to} maxLength={8} onChange={(e) => upd({ id_prefix_to: e.target.value.trim() })} placeholder="0" />
                  </Field>
                  <Field label="Giữ số cuối (0 = tất cả)">
                    <Input type="number" min={0} max={20} value={src.id_keep_last ?? 0} onChange={(e) => upd({ id_keep_last: Math.min(20, Math.max(0, Number(e.target.value) || 0)) })} />
                  </Field>
                  <Button size="icon" variant="ghost" aria-label="Bỏ hệ thống BioStar này" onClick={() => set("biostar_sources", v.biostar_sources.filter((_, j) => j !== k))}>
                    <Trash2 className="size-4" aria-hidden="true" />
                  </Button>
                  {(src.id_prefix || src.id_keep_last > 0) && (
                    <p className="text-sm text-muted sm:col-span-5">
                      Ví dụ:{" "}
                      {[`${src.id_prefix}00002`, `${src.id_prefix}002352`].map((ex, j) => (
                        <span key={ex}>{j > 0 && " · "}user <span className="font-mono">{ex}</span> → Mã NV <span className="font-mono">{mapId(ex, src)}</span></span>
                      ))}
                    </p>
                  )}
                </li>
              );
            })}
          </ul>
          <Button
            className="mt-3"
            disabled={profileOptions("biostar").length === 0 || v.biostar_sources.length >= 5}
            onClick={() => {
              const other = profileOptions("biostar").find((p) => p.id !== v.biostar_profile_id) ?? profileOptions("biostar")[0]!;
              set("biostar_sources", [...v.biostar_sources, { profile_id: other.id, id_prefix: "", id_prefix_to: "", id_keep_last: 0 }]);
            }}
          >
            <Plus className="size-4" aria-hidden="true" />Thêm hệ thống BioStar
          </Button>
          <Field
            label="Nhóm nghỉ việc trên BioStar"
            className="mt-5 max-w-xl"
            hint="Tên nhóm người dùng, cách nhau dấu phẩy (vd. 0.NGHIVIEC). User trong nhóm này coi như đã nghỉ việc: không đối soát với SDP, không báo “còn hoạt động”."
          >
            <Input value={retiredGroups} onChange={(e) => setRetiredGroups(e.target.value)} placeholder="0.NGHIVIEC" />
          </Field>
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
            <Field label="Giữ kết quả đối soát (ngày)" hint="Dữ liệu thô của SDP/BioStar chỉ giữ cho vài lần đồng bộ gần nhất.">
              <Input type="number" min={7} max={730} value={v.snapshot_retention_days} onChange={num("snapshot_retention_days")} className="w-28" />
            </Field>
          </div>
        </Panel>

        <Panel title="Job onboard / offboard" description="Các bước ghi dữ liệu dùng credential Write của từng kết nối.">
          <div className="space-y-4">
            <Switch label="Tự tạo Job nháp sau mỗi lần đồng bộ" description="Mỗi request Nhân viên mới / Nghỉ việc có một Job nháp. Job nháp không thay đổi gì trên SDP hay BioStar." checked={v.auto_create_jobs} onChange={(x) => set("auto_create_jobs", x)} />
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Trạng thái request khi đóng Job" hint="Tên trạng thái trên SDP, ví dụ Resolved hoặc Closed.">
                <Input value={v.sdp_close_status} onChange={(e) => set("sdp_close_status", e.target.value)} />
              </Field>
              <Field label="Trạng thái được coi là đã đóng" hint="Cách nhau bằng dấu phẩy. Job dừng nếu request đang ở các trạng thái này.">
                <Input value={closedStatuses} onChange={(e) => setClosedStatuses(e.target.value)} />
              </Field>
            </div>
            <Switch label="Thêm ghi chú tóm tắt vào request khi đóng" checked={v.sdp_add_note} onChange={(x) => set("sdp_add_note", x)} />
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Đường dẫn API đính kèm file" hint="{request_id} được thay bằng ID request. Kiểm tra với bản SDP đang dùng.">
                <Input value={v.sdp_attach_path} onChange={(e) => set("sdp_attach_path", e.target.value)} className="font-mono" />
              </Field>
              <Field label="Tên field file">
                <Input value={v.sdp_attach_field} onChange={(e) => set("sdp_attach_field", e.target.value)} className="font-mono" />
              </Field>
            </div>
            <fieldset className="rounded-sm border border-line p-3">
              <legend className="px-1 text-sm font-semibold">BioStar</legend>
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <Button size="sm" onClick={() => setLoadGroups(true)} loading={groups.isFetching}>Tải danh sách nhóm từ BioStar</Button>
                {groups.isError && <span className="text-sm text-failed">{(groups.error as Error).message}</span>}
              </div>
              <div className="grid gap-4 sm:grid-cols-2">
                <Field label="Nhóm người dùng cho nhân viên mới">
                  {groups.data ? (
                    <Select value={v.biostar_user_group_id} onChange={(e) => set("biostar_user_group_id", e.target.value)}>
                      {groups.data.user_groups.map((g) => <option key={g.id} value={g.id}>{g.name} (ID {g.id})</option>)}
                    </Select>
                  ) : (
                    <Input value={v.biostar_user_group_id} onChange={(e) => set("biostar_user_group_id", e.target.value)} className="font-mono" />
                  )}
                </Field>
                <Field label="Hiệu lực user tới ngày">
                  <DateInput value={v.biostar_expiry_date} onChange={(x) => x && set("biostar_expiry_date", x)} />
                </Field>
              </div>
              <fieldset className="mt-3">
                <legend className="mb-1 text-sm font-medium">Nhóm quyền ra vào mặc định</legend>
                {groups.data ? (
                  <div className="grid gap-1 sm:grid-cols-2">
                    {groups.data.access_groups.map((g) => (
                      <label key={g.id} className="flex cursor-pointer items-center gap-2 text-sm">
                        <input type="checkbox" className="size-4 accent-[var(--primary)]" checked={v.biostar_access_group_ids.includes(g.id)}
                          onChange={(e) => set("biostar_access_group_ids", e.target.checked ? [...v.biostar_access_group_ids, g.id] : v.biostar_access_group_ids.filter((x) => x !== g.id))} />
                        {g.name} <span className="text-muted">(ID {g.id})</span>
                      </label>
                    ))}
                  </div>
                ) : (
                  <p className="text-sm text-muted">Đang chọn ID: {v.biostar_access_group_ids.join(", ") || "chưa có"}. Tải danh sách từ BioStar để chọn.</p>
                )}
              </fieldset>
              <Field label="Khi offboard" className="mt-3 max-w-sm">
                <Select value={v.biostar_offboard_action} onChange={(e) => set("biostar_offboard_action", e.target.value as "disable" | "delete")}>
                  <option value="disable">Vô hiệu hoá user và gỡ nhóm quyền (giữ lịch sử)</option>
                  <option value="delete">Xoá user khỏi BioStar</option>
                </Select>
              </Field>
            </fieldset>
          </div>
        </Panel>

        <Panel
          title={
            <span className="inline-flex items-center gap-1">
              Tiêu chuẩn cấp phát thiết bị theo band
              <HelpTip title="Tiêu chuẩn cấp phát">
                <span className="block">Mỗi dòng là một thiết bị nhân viên mới được nhận. <b>Band</b> để trống = áp dụng mọi band; nhiều band cách nhau dấu phẩy (vd. B4, B5).</span>
                <span className="block"><b>Loại tài sản SDP</b>: product type trên ServiceDesk Plus được tính là đáp ứng dòng này (vd. Laptop). <b>Model chứa</b> (tuỳ chọn) lọc thêm theo tên model, vd. “XPS”.</span>
                <span className="block"><b>Tên trong bảng giá</b>: thiết bị được điền sẵn khi đề xuất mua mới.</span>
                <span className="block">Band của nhân viên lấy từ dòng “{v.field_labels.band || "Band"}” trong request RTR (đổi nhãn ở mục Request RTR).</span>
              </HelpTip>
            </span>
          }
          description="Dùng ở trang Kho & cấp phát để gợi ý tận dụng tài sản In Store hoặc đề xuất mua mới."
        >
          <div className="space-y-3">
            {std.map((r, i) => {
              const upd = (patch: Partial<StdRow>) => setStd((cur) => cur.map((x, j) => (j === i ? { ...x, ...patch } : x)));
              return (
                <fieldset key={i} className="grid gap-2 rounded-sm border border-line p-3 sm:grid-cols-[1.2fr_1fr_1fr_1fr_5rem_1.2fr_auto]">
                  <legend className="sr-only">Dòng tiêu chuẩn {i + 1}</legend>
                  <Field label="Thiết bị"><Input value={r.label} maxLength={100} onChange={(e) => upd({ label: e.target.value })} /></Field>
                  <Field label="Band"><Input value={r.bands} placeholder="Mọi band" onChange={(e) => upd({ bands: e.target.value })} /></Field>
                  <Field label="Loại tài sản SDP"><Input value={r.product_types} onChange={(e) => upd({ product_types: e.target.value })} /></Field>
                  <Field label="Model chứa"><Input value={r.product_contains} maxLength={100} onChange={(e) => upd({ product_contains: e.target.value })} /></Field>
                  <Field label="SL"><Input type="number" min={1} max={10} value={r.quantity} onChange={(e) => upd({ quantity: Number(e.target.value) })} /></Field>
                  <Field label="Tên trong bảng giá"><Input value={r.catalog_name} maxLength={300} onChange={(e) => upd({ catalog_name: e.target.value })} /></Field>
                  <div className="flex items-end">
                    <Button size="icon" variant="ghost" aria-label={`Xoá dòng ${r.label || i + 1}`} onClick={() => setStd((cur) => cur.filter((_, j) => j !== i))}>
                      <Trash2 className="size-4" aria-hidden="true" />
                    </Button>
                  </div>
                </fieldset>
              );
            })}
            <div className="flex flex-wrap items-end gap-4">
              <Button onClick={() => setStd((cur) => [...cur, { label: "", bands: "", product_types: "", product_contains: "", quantity: 1, catalog_name: "" }])}>
                <Plus className="size-4" aria-hidden="true" />Thêm dòng
              </Button>
              <Field label="Bỏ qua onboard có ngày hiệu lực cũ hơn (ngày)" className="w-64">
                <Input type="number" min={0} max={365} value={v.allocation_past_days} onChange={num("allocation_past_days")} />
              </Field>
            </div>
          </div>
        </Panel>

        <Panel title="Biên bản" description="Dùng khi lập Biên bản Bàn giao / Thu hồi.">
          <Field label="Người kiểm tra (Nội dung B)" className="max-w-md">
            <Input value={v.reviewer_name} onChange={(e) => set("reviewer_name", e.target.value)} placeholder="Họ tên trưởng nhóm IT" />
          </Field>
        </Panel>

        {serverError && <p role="alert" className="rounded-sm bg-failed-soft px-3 py-2 text-sm text-failed">{serverError}</p>}
        <div className="sticky bottom-0 -mx-5 flex justify-end gap-2 border-t border-line bg-paper/95 px-5 py-3 backdrop-blur">
          <Button onClick={() => { setV(d.values); setProductTypes(d.values.asset_product_types.join(", ")); setClosedStatuses(d.values.sdp_closed_statuses.join(", ")); setRetiredGroups(d.values.biostar_retired_groups.join(", ")); setStd(toRows(d.values.allocation_standard)); }}>Hoàn tác thay đổi</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>Lưu cấu hình</Button>
        </div>
      </form>
    </>
  );
}
