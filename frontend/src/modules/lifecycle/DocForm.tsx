import { Plus, Trash2 } from "lucide-react";
import { Button } from "@/core/ui/button";
import { DateInput } from "@/core/ui/date-input";
import { Panel } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { cn } from "@/core/lib/cn";
import { isoDateVn } from "@/core/lib/format";
import type { AssetLine, Check, Condition, DocData, Meta, ShortKind } from "./docs";

export function emptyData(kind: ShortKind, meta: Meta, defaults: Partial<DocData> = {}): DocData {
  const today = isoDateVn();
  return {
    employee: { id: "", full_name: "", department: "", job_title: "", work_location: "" },
    date: today,
    it_rep: "",
    reviewer: "",
    request_id: null,
    assets: [{ name: "", tag: "", date: today, note: "", ...(kind === "return" ? { condition: "good" as Condition } : {}) }],
    checklist: (kind === "handover" ? meta.handover_checklist : meta.return_checklist).map(() => "" as Check),
    ...(kind === "handover" ? { software: meta.handover_software.map(() => false), software_other: "" } : {}),
    ...defaults,
  };
}

export function formErrors(d: DocData): Record<string, string> {
  const e: Record<string, string> = {};
  if (!/^[0-9A-Za-z]+$/.test(d.employee.id)) e.emp_id = "Nhập Mã NV (chỉ gồm chữ và số).";
  if (!d.employee.full_name.trim()) e.emp_name = "Nhập họ tên nhân viên.";
  if (!d.date) e.date = "Nhập ngày theo dạng dd/mm/yyyy.";
  if (!d.it_rep.trim()) e.it_rep = "Nhập họ tên đại diện IT.";
  if (!d.assets.length) e.assets = "Thêm ít nhất một thiết bị.";
  d.assets.forEach((a, i) => {
    if (!a.name.trim()) e[`asset_${i}`] = "Nhập tên tài sản.";
  });
  if (d.request_id && !/^\d+$/.test(d.request_id)) e.request_id = "Số request chỉ gồm chữ số.";
  return e;
}

interface Props {
  kind: ShortKind;
  value: DocData;
  onChange: (d: DocData) => void;
  meta: Meta;
  readOnly: boolean;
  errors: Record<string, string>;
  showErrors: boolean;
}

export function DocForm({ kind, value: d, onChange, meta, readOnly, errors, showErrors }: Props) {
  const set = (patch: Partial<DocData>) => onChange({ ...d, ...patch });
  const emp = (patch: Partial<DocData["employee"]>) => set({ employee: { ...d.employee, ...patch } });
  const asset = (i: number, patch: Partial<AssetLine>) => set({ assets: d.assets.map((a, j) => (j === i ? { ...a, ...patch } : a)) });
  const checklist = kind === "handover" ? meta.handover_checklist : meta.return_checklist;
  const dateLabel = kind === "handover" ? "Ngày bàn giao" : "Ngày thu hồi";

  return (
    <fieldset disabled={readOnly} className="space-y-4">
      <legend className="sr-only">Nội dung biên bản</legend>
      <Panel title="Thông tin nhân viên">
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          <Field label="Mã nhân viên" required error={errors.emp_id} showError={showErrors}>
            <Input value={d.employee.id} onChange={(e) => emp({ id: e.target.value.trim() })} className="font-mono" />
          </Field>
          <Field label={kind === "handover" ? "Người nhận" : "Người bàn giao lại"} required error={errors.emp_name} showError={showErrors}>
            <Input value={d.employee.full_name} onChange={(e) => emp({ full_name: e.target.value })} />
          </Field>
          <Field label="Phòng ban">
            <Input value={d.employee.department} onChange={(e) => emp({ department: e.target.value })} />
          </Field>
          <Field label="Chức danh">
            <Input value={d.employee.job_title} onChange={(e) => emp({ job_title: e.target.value })} />
          </Field>
          <Field label="Địa điểm làm việc">
            <Input value={d.employee.work_location} onChange={(e) => emp({ work_location: e.target.value })} />
          </Field>
          <div className="grid grid-cols-2 gap-4">
            <Field label={dateLabel} required error={errors.date} showError={showErrors}>
              <DateInput value={d.date} onChange={(v) => set({ date: v })} className="w-full tabular" />
            </Field>
            <Field label="Request SDP" hint="Số ID request để đính kèm bản ký." error={errors.request_id} showError={showErrors}>
              <Input value={d.request_id ?? ""} inputMode="numeric" onChange={(e) => set({ request_id: e.target.value.trim() || null })} className="font-mono" />
            </Field>
          </div>
        </div>
      </Panel>

      <Panel
        title={kind === "handover" ? "Danh mục thiết bị bàn giao" : "Danh mục thiết bị thu hồi"}
        description={kind === "return" ? "Thiết bị nhân viên chưa trả: chọn tình trạng “Chưa thu hồi” – vẫn in trên biên bản." : undefined}
        actions={
          !readOnly && (
            <Button
              size="sm"
              onClick={() => set({ assets: [...d.assets, { name: "", tag: "", date: d.date, note: "", ...(kind === "return" ? { condition: "good" as Condition } : {}) }] })}
            >
              <Plus className="size-4" aria-hidden="true" />
              Thêm thiết bị
            </Button>
          )
        }
        bodyClassName="p-0"
      >
        <div className="overflow-x-auto">
          <table className="w-full text-left">
            <caption className="sr-only">Danh mục thiết bị</caption>
            <thead className="bg-surface-2 text-sm text-muted">
              <tr>
                <th scope="col" className="w-10 px-3 py-2 font-semibold">STT</th>
                <th scope="col" className="px-2 py-2 font-semibold">Tên tài sản</th>
                <th scope="col" className="px-2 py-2 font-semibold">Asset tag</th>
                <th scope="col" className="px-2 py-2 font-semibold">{kind === "handover" ? "Ngày cấp" : "Ngày thu hồi"}</th>
                {kind === "return" && <th scope="col" className="px-2 py-2 font-semibold">Tình trạng</th>}
                <th scope="col" className="px-2 py-2 font-semibold">Ghi chú</th>
                {!readOnly && <th scope="col" className="w-10 px-2 py-2"><span className="sr-only">Xoá</span></th>}
              </tr>
            </thead>
            <tbody>
              {d.assets.map((a, i) => {
                const err = showErrors ? errors[`asset_${i}`] : undefined;
                return (
                  <tr key={i} className="border-t border-line align-top">
                    <td className="px-3 py-2 tabular">{i + 1}</td>
                    <td className="px-2 py-1.5">
                      <Input aria-label={`Tên tài sản dòng ${i + 1}`} aria-invalid={err ? "true" : "false"} value={a.name} onChange={(e) => asset(i, { name: e.target.value })} />
                      {err && <p className="mt-0.5 text-xs text-failed">{err}</p>}
                    </td>
                    <td className="px-2 py-1.5"><Input aria-label={`Asset tag dòng ${i + 1}`} value={a.tag} onChange={(e) => asset(i, { tag: e.target.value })} className="font-mono" /></td>
                    <td className="px-2 py-1.5"><DateInput aria-label={`Ngày dòng ${i + 1}`} value={a.date ?? ""} onChange={(v) => asset(i, { date: v || null })} /></td>
                    {kind === "return" && (
                      <td className="px-2 py-1.5">
                        <Select aria-label={`Tình trạng dòng ${i + 1}`} value={a.condition ?? "good"} onChange={(e) => asset(i, { condition: e.target.value as Condition })}>
                          {Object.entries(meta.conditions).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                        </Select>
                      </td>
                    )}
                    <td className="px-2 py-1.5"><Input aria-label={`Ghi chú dòng ${i + 1}`} value={a.note} onChange={(e) => asset(i, { note: e.target.value })} /></td>
                    {!readOnly && (
                      <td className="px-2 py-1.5">
                        <Button variant="ghost" size="icon" aria-label={`Xoá dòng ${i + 1}`} disabled={d.assets.length === 1} onClick={() => set({ assets: d.assets.filter((_, j) => j !== i) })}>
                          <Trash2 className="size-4" aria-hidden="true" />
                        </Button>
                      </td>
                    )}
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </Panel>

      <Panel title="Nội dung B – Checklist" description="Kết quả được in sẵn trên biên bản (☑ / ☐).">
        <ol className="divide-y divide-line">
          {checklist.map((item, i) => (
            <li key={i} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span className="min-w-0 flex-1"><span className="mr-2 text-muted tabular">{i + 1}.</span>{item}</span>
              <span role="radiogroup" aria-label={`Mục ${i + 1}: ${item}`} className="flex gap-1">
                {([["ok", "OK"], ["na", "N/A"], ["", "Chưa chọn"]] as const).map(([v, label]) => (
                  <label
                    key={v || "none"}
                    className={cn(
                      "flex h-7 cursor-pointer items-center gap-1.5 rounded-sm border px-2 text-sm",
                      d.checklist[i] === v ? "border-primary bg-primary-soft text-primary" : "border-line-strong",
                    )}
                  >
                    <input
                      type="radio"
                      name={`checklist-${i}`}
                      className="accent-[var(--primary)]"
                      checked={d.checklist[i] === v}
                      onChange={() => set({ checklist: d.checklist.map((c, j) => (j === i ? v : c)) })}
                    />
                    {label}
                  </label>
                ))}
              </span>
            </li>
          ))}
        </ol>
        {kind === "handover" && d.software && (
          <fieldset className="mt-4">
            <legend className="mb-2 text-base font-semibold">Phần mềm đã cài</legend>
            <div className="grid gap-1.5 sm:grid-cols-2">
              {meta.handover_software.map((name, i) => (
                <label key={i} className="flex cursor-pointer items-center gap-2">
                  <input
                    type="checkbox"
                    className="size-4 accent-[var(--primary)]"
                    checked={d.software![i] ?? false}
                    onChange={(e) => set({ software: d.software!.map((s, j) => (j === i ? e.target.checked : s)) })}
                  />
                  {name}
                </label>
              ))}
            </div>
            <Field label="Phần mềm khác" className="mt-3 max-w-md">
              <Input value={d.software_other ?? ""} onChange={(e) => set({ software_other: e.target.value })} />
            </Field>
          </fieldset>
        )}
      </Panel>

      <Panel title="Ký xác nhận">
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Đại diện IT" required error={errors.it_rep} showError={showErrors}>
            <Input value={d.it_rep} onChange={(e) => set({ it_rep: e.target.value })} />
          </Field>
          <Field label="Người kiểm tra (Nội dung B)" hint="Mặc định lấy từ Cấu hình module.">
            <Input value={d.reviewer} onChange={(e) => set({ reviewer: e.target.value })} />
          </Field>
        </div>
      </Panel>
    </fieldset>
  );
}
