import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api, errorDetails } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select, Switch } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { Plus, Trash2, X } from "lucide-react";
import { DateInput } from "@/core/ui/date-input";
import { announce } from "@/core/ui/live-region";
import type { Kind } from "../api";
import { KIND_LABEL } from "../labels";
import { AllocationMatrixEditor, TiersEditor, type DeviceTier, type Matrix } from "./AllocationMatrix";

interface Values {
  sdp_profile_id: string | null;
  biostar_profile_id: string | null;
  biostar_sources: BioStarSource[];
  biostar_excluded_groups: string[];
  rtr_requester_name: string;
  rtr_lookback_days: number;
  field_labels: Record<string, string>;
  change_type_map: Record<string, Kind>;
  sdp_user_employee_id_field: string;
  match_assets_by_name: boolean;
  asset_product_types: string[];
  asset_state_in_use: string;
  asset_state_in_store: string;
  asset_state_pending_disposal: string;
  asset_state_disposed: string;
  asset_fa_code_field: string;
  onboard_grace_days: number;
  offboard_grace_days: number;
  snapshot_retention_days: number;
  reviewer_name: string;
  hr_review_to: string[];
  hr_review_cc: string[];
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
  allocation_matrix: Matrix;
  device_tiers: DeviceTier[];
  allocation_past_days: number;
}

interface BioStarSource {
  profile_id: string;
}


interface StandardItem {
  label: string;
  bands: string[];
  product_types: string[];
  product_contains: string;
  quantity: number;
  catalog_name: string;
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
  const [hrTo, setHrTo] = useState("");
  const [hrCc, setHrCc] = useState("");
  const [serverError, setServerError] = useState("");
  const [closedStatuses, setClosedStatuses] = useState("");
  const [excludedGroups, setExcludedGroups] = useState("");
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
      setExcludedGroups(q.data.values.biostar_excluded_groups.join(", "));
      setHrTo(q.data.values.hr_review_to.join(", "));
      setHrCc(q.data.values.hr_review_cc.join(", "));
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
      const msg = errorDetails(e);
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
      biostar_excluded_groups: list(excludedGroups),
      hr_review_to: list(hrTo),
      hr_review_cc: list(hrCc),
      device_tiers: v.device_tiers.filter((t) => t.name.trim()).map((t) => ({ ...t, name: t.name.trim() })),
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
              <span className="block">Mã NV được so theo 5 số cuối trên mọi BioStar và SDP (vd. user 100008 → Mã NV 000008); Mã NV và họ tên lấy theo SDP.</span>
              <span className="block">Một người có user ở cả hai hệ thống: user đang hoạt động được ưu tiên để không bỏ sót người đã nghỉ còn quyền ra vào.</span>
            </HelpTip>
          </h3>
          <p className="mb-3 text-sm text-muted">Mã NV so theo 5 số cuối với SDP (vd. 100008 → 000008); Mã NV và họ tên lấy theo SDP.</p>
          <ul className="space-y-3">
            {v.biostar_sources.map((src, k) => {
              const upd = (patch: Partial<BioStarSource>) => set("biostar_sources", v.biostar_sources.map((x, j) => (j === k ? { ...x, ...patch } : x)));
              return (
                <li key={k} className="grid items-end gap-3 rounded-sm border border-line p-3 sm:grid-cols-[1fr_auto]">
                  <Field label="Kết nối BioStar">
                    <Select value={src.profile_id} onChange={(e) => upd({ profile_id: e.target.value })}>
                      {profileOptions("biostar").map((p) => <option key={p.id} value={p.id}>{p.name}{p.enabled ? "" : " (đang tắt)"}</option>)}
                    </Select>
                  </Field>
                  <Button size="icon" variant="ghost" aria-label="Bỏ hệ thống BioStar này" onClick={() => set("biostar_sources", v.biostar_sources.filter((_, j) => j !== k))}>
                    <Trash2 className="size-4" aria-hidden="true" />
                  </Button>
                </li>
              );
            })}
          </ul>
          <Button
            className="mt-3"
            disabled={profileOptions("biostar").length === 0 || v.biostar_sources.length >= 5}
            onClick={() => {
              const other = profileOptions("biostar").find((p) => p.id !== v.biostar_profile_id) ?? profileOptions("biostar")[0]!;
              set("biostar_sources", [...v.biostar_sources, { profile_id: other.id }]);
            }}
          >
            <Plus className="size-4" aria-hidden="true" />Thêm hệ thống BioStar
          </Button>
          <GroupPicker
            id="excluded-groups"
            label="Nhóm loại trừ khỏi đối soát"
            hint="Người đã nghỉ việc (chuyển sang nhóm nghỉ việc thay vì xoá), nhà cung cấp, khách, nhà thầu…: không đối soát với SDP, không báo “còn hoạt động” hay “không có trên SDP”. Nếu còn request RTR liên quan, người đó vẫn hiện với trạng thái “Nhóm loại trừ”."
            placeholder="0.NGHIVIEC"
            value={excludedGroups}
            onChange={setExcludedGroups}
            className="mt-5"
          />
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
            <Field label="Trạng thái “chờ thanh lý”" hint="Tên trạng thái tài sản đã tạo trên SDP (trang Tồn kho).">
              <Input value={v.asset_state_pending_disposal} onChange={(e) => set("asset_state_pending_disposal", e.target.value)} />
            </Field>
            <Field label="Trạng thái “đã thanh lý”" hint="Thường là Disposed trên SDP.">
              <Input value={v.asset_state_disposed} onChange={(e) => set("asset_state_disposed", e.target.value)} />
            </Field>
            <Field label="Trường SDP chứa Mã TSCĐ" hint="Khoá của trường tuỳ chỉnh trên tài sản SDP (vd. udf_char1) – dùng khi Kiểm kê để khớp với sổ Kế toán. Để trống nếu chưa có.">
              <Input value={v.asset_fa_code_field} onChange={(e) => set("asset_fa_code_field", e.target.value.trim())} className="font-mono" />
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
              Ma trận cấp phát thiết bị
              <HelpTip title="Ma trận cấp phát">
                <span className="block"><b>Hàng</b> là cấp bậc (nhóm band), <b>cột</b> là thiết bị. Tích ô để cấp thiết bị đó cho cấp bậc đó, chọn số lượng và cấu hình tham chiếu.</span>
                <span className="block">Bấm tên hàng / cột để sửa: band đúng như HR ghi ở dòng “{v.field_labels.band || "Band"}” trong request RTR, loại tài sản SDP, tên trong bảng giá. Hàng để trống band nhận mọi band chưa có ở hàng khác; hàng giới hạn phòng ban thay cho hàng chung cùng band.</span>
                <span className="block">Trang Kho thiết bị dùng ma trận để gợi ý lấy tài sản trong kho (hàng thu hồi trước) hoặc đề xuất mua; Playbook dùng để so “số lượng theo tiêu chuẩn band”.</span>
              </HelpTip>
            </span>
          }
          description="Tiêu chuẩn cho nhân viên mới (Onboarding). Thiết bị ngoài ô đã tích đi theo Adhoc / Exception và cần phê duyệt."
        >
          <AllocationMatrixEditor value={v.allocation_matrix} tiers={v.device_tiers} onChange={(m) => set("allocation_matrix", m)} />
          <Field label="Bỏ qua onboard có ngày hiệu lực cũ hơn (ngày)" className="mt-4 w-64">
            <Input type="number" min={0} max={365} value={v.allocation_past_days} onChange={num("allocation_past_days")} />
          </Field>
        </Panel>

        <Panel
          title="Cấu hình thiết bị tham chiếu"
          description="Cấu hình tối thiểu cho từng loại máy; ô trong ma trận chọn theo tên ở đây. Đổi tên cấu hình thì ma trận đổi theo."
        >
          <TiersEditor
            value={v.device_tiers}
            used={new Set(v.allocation_matrix.cells.flat().filter((c) => c.on && c.tier).map((c) => c.tier))}
            onChange={(next) => {
              // A renamed tier keeps the matrix cells that use it.
              const renamed = new Map(next.length !== v.device_tiers.length ? [] : v.device_tiers.map((t, i) => [t.name, next[i]?.name]).filter(([a, b]) => b !== undefined && a !== b) as [string, string][]);
              setV((cur) =>
                cur && {
                  ...cur,
                  device_tiers: next,
                  allocation_matrix: renamed.size
                    ? { ...cur.allocation_matrix, cells: cur.allocation_matrix.cells.map((r) => r.map((c) => (renamed.has(c.tier) ? { ...c, tier: renamed.get(c.tier)! } : c))) }
                    : cur.allocation_matrix,
                },
              );
            }}
          />
        </Panel>

        <Panel title="Biên bản" description="Dùng khi lập Biên bản Bàn giao / Thu hồi.">
          <Field label="Người kiểm tra (Nội dung B)" className="max-w-md">
            <Input value={v.reviewer_name} onChange={(e) => set("reviewer_name", e.target.value)} placeholder="Họ tên trưởng nhóm IT" />
          </Field>
        </Panel>

        <Panel title="Rà soát nghỉ việc hằng tháng (ISO 27001)" description="Người nhận email nháp gửi phòng Nhân sự. Nhiều địa chỉ cách nhau bằng dấu phẩy.">
          <div className="grid gap-4 sm:grid-cols-2">
            <Field label="Gửi đến (To)">
              <Input type="text" inputMode="email" value={hrTo} onChange={(e) => setHrTo(e.target.value)} placeholder="hr@congty.com.vn" />
            </Field>
            <Field label="Đồng gửi (Cc)">
              <Input type="text" inputMode="email" value={hrCc} onChange={(e) => setHrCc(e.target.value)} placeholder="it-manager@congty.com.vn" />
            </Field>
          </div>
        </Panel>

        {serverError && <p role="alert" className="rounded-sm bg-failed-soft px-3 py-2 text-sm text-failed">{serverError}</p>}
        <div className="sticky bottom-0 -mx-4 sm:-mx-5 flex justify-end gap-2 border-t border-line bg-paper/95 px-5 py-3 backdrop-blur">
          <Button onClick={() => { setV(d.values); setProductTypes(d.values.asset_product_types.join(", ")); setClosedStatuses(d.values.sdp_closed_statuses.join(", ")); setExcludedGroups(d.values.biostar_excluded_groups.join(", ")); setHrTo(d.values.hr_review_to.join(", ")); setHrCc(d.values.hr_review_cc.join(", ")); }}>Hoàn tác thay đổi</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>Lưu cấu hình</Button>
        </div>
      </form>
    </>
  );
}

const splitGroups = (text: string) => text.split(",").map((x) => x.trim()).filter(Boolean);

/** BioStar user groups as removable chips; add one from the groups read live from BioStar, or by name. */
function GroupPicker({ id, label, hint, placeholder, value, onChange, className }: {
  id: string;
  label: string;
  hint: string;
  placeholder: string;
  value: string;
  onChange: (next: string) => void;
  className?: string;
}) {
  const groups = useQuery({
    queryKey: ["lifecycle", "biostar-user-groups"],
    queryFn: () => api<{ items: { name: string; users: number | null }[]; errors: string[] }>("/api/lifecycle/biostar/user-groups"),
    staleTime: 60_000,
  });
  const [draft, setDraft] = useState("");
  const chosen = splitGroups(value);
  const known = new Set(chosen.map((g) => g.toLowerCase()));
  const add = (raw: string) => {
    const name = raw.trim();
    if (name && !name.includes(",") && !known.has(name.toLowerCase())) onChange([...chosen, name].join(", "));
    setDraft("");
  };
  const available = (groups.data?.items ?? []).filter((g) => !known.has(g.name.toLowerCase()));
  return (
    <fieldset className={`max-w-3xl ${className ?? ""}`}>
      <legend className="mb-1.5 text-sm font-medium">{label}</legend>
      <p className="mb-2 text-sm text-muted">{hint}</p>
      {chosen.length > 0 ? (
        <ul className="mb-2 flex flex-wrap gap-1.5" aria-label={`${label} đã chọn`}>
          {chosen.map((g) => (
            <li key={g} className="inline-flex h-7 items-center gap-1 rounded-full border border-primary bg-primary-soft pl-2.5 pr-1 text-sm text-primary">
              {g}
              <button type="button" className="inline-flex size-6 cursor-pointer items-center justify-center rounded-full hover:bg-surface" aria-label={`Bỏ nhóm ${g}`} onClick={() => onChange(chosen.filter((x) => x !== g).join(", "))}>
                <X className="size-3.5" aria-hidden="true" />
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mb-2 text-sm text-muted">Chưa chọn nhóm nào.</p>
      )}
      <p className="mb-1 text-xs text-muted">Nhóm trên BioStar – bấm để thêm:</p>
      {groups.isLoading && <p className="text-sm text-muted">Đang đọc nhóm từ BioStar…</p>}
      {available.length > 0 && (
        <ul className="mb-2 flex max-h-40 flex-wrap gap-1.5 overflow-y-auto" aria-label={`Nhóm BioStar có thể thêm vào ${label.toLowerCase()}`}>
          {available.map((g) => (
            <li key={g.name}>
              <button type="button" onClick={() => add(g.name)} className="inline-flex h-7 cursor-pointer items-center gap-1 rounded-full border border-line-strong bg-surface px-2.5 text-sm hover:bg-surface-2">
                <Plus className="size-3.5" aria-hidden="true" />
                {g.name}
                {g.users != null && <span className="text-muted tabular">({g.users})</span>}
              </button>
            </li>
          ))}
        </ul>
      )}
      {groups.data && groups.data.errors.length > 0 && (
        <p role="status" className="mb-2 text-sm text-waiting">Không đọc được nhóm từ một hệ thống BioStar: {groups.data.errors.join("; ")}. Danh sách lấy theo lần đồng bộ gần nhất.</p>
      )}
      <div className="flex gap-2">
        <Input
          id={id}
          aria-label={`Thêm tên nhóm khác vào ${label.toLowerCase()}`}
          value={draft}
          placeholder={`Hoặc gõ tên nhóm, vd. ${placeholder}`}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              add(draft);
            }
          }}
        />
        <Button type="button" onClick={() => add(draft)} disabled={!draft.trim()}>
          <Plus className="size-4" aria-hidden="true" />Thêm
        </Button>
      </div>
      <p className="mt-1 text-xs text-muted">Thay đổi có hiệu lực sau khi bấm Lưu và chạy đồng bộ.</p>
    </fieldset>
  );
}
