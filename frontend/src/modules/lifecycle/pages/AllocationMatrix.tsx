import { useState } from "react";
import { LayoutGrid, Pencil, Plus, Trash2 } from "lucide-react";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input, Select } from "@/core/ui/field";
import { cn } from "@/core/lib/cn";

export interface DeviceTier {
  name: string;
  cpu: string;
  ram: string;
  storage: string;
  description: string;
  catalog_name: string;
}
export interface MatrixDevice {
  label: string;
  product_types: string[];
  product_contains: string;
  catalog_name: string;
  note: string;
}
export interface MatrixLevel {
  name: string;
  bands: string[];
  departments: string[];
  group: string;
}
export interface MatrixCell {
  on: boolean;
  quantity: number;
  tier: string;
}
export interface Matrix {
  devices: MatrixDevice[];
  levels: MatrixLevel[];
  cells: MatrixCell[][];
}

const list = (text: string) => text.split(",").map((s) => s.trim()).filter(Boolean);
const fold = (s: string) => s.normalize("NFD").replace(/\p{M}/gu, "").toLowerCase().replace(/\s+/g, "");
const emptyCell = (): MatrixCell => ({ on: false, quantity: 1, tier: "" });

/** Tiers offered in a column: those named after the device (e.g. "PC - Standard" under PC); none for a monitor. */
export function tiersFor(device: MatrixDevice, tiers: DeviceTier[]): DeviceTier[] {
  return tiers.filter((t) => fold(t.name).startsWith(fold(device.label)) || device.product_types.some((p) => fold(t.name).startsWith(fold(p))));
}

/** Bands listed by more than one general row (department rows may repeat a band on purpose). */
export function overlappingBands(levels: MatrixLevel[]): string[] {
  const seen = new Map<string, number>();
  for (const lv of levels) if (!lv.departments.length) for (const b of new Set(lv.bands.map(fold))) seen.set(b, (seen.get(b) ?? 0) + 1);
  return levels.flatMap((lv) => (lv.departments.length ? [] : lv.bands)).filter((b, i, all) => (seen.get(fold(b)) ?? 0) > 1 && all.indexOf(b) === i);
}

const on = (tier = "", quantity = 1): MatrixCell => ({ on: true, quantity, tier });
const no = emptyCell;
const device = (label: string, product_types: string[], note = ""): MatrixDevice => ({ label, product_types, product_contains: "", catalog_name: "", note });

/** A starting matrix shaped like the company's allocation sheet (5 levels × 8 devices). Bands are examples: numbers or
 * titles exactly as the RTR band line reads. */
export const PRESET: Matrix = {
  devices: [
    device("PC", ["Desktop"]),
    device("Laptop", ["Laptop"], "Mặc định không – cấp theo RTR, Adhoc / Exception hoặc tận dụng máy cũ theo phê duyệt"),
    device("Màn hình", ["Monitor"], "24\" FHD"),
    device("Bàn phím", ["Keyboard"]),
    device("Chuột", ["Mouse"]),
    device("Headset", ["Headset"]),
    device("Điện thoại bàn", ["IP Phone"]),
    device("Phụ kiện khác", ["Accessory"], "Theo nhu cầu công việc (bàn nâng, tay arm, màn hình thứ 3 trở lên) – Adhoc"),
  ],
  levels: [
    { name: "Intern / Vendor", group: "Thực tập sinh / Đối tác", bands: [], departments: [] },
    { name: "Band 1–6", group: "Nhân viên", bands: ["1", "2", "3", "4", "5", "6"], departments: [] },
    { name: "Band 4–6 kỹ thuật", group: "Nhân viên kỹ thuật (IT / DTI / DATA)", bands: ["4", "5", "6"], departments: ["IT", "DTI", "DATA"] },
    { name: "Band 7–9", group: "Manager / Senior Manager / Director", bands: ["7", "8", "9", "Manager / Senior Manager"], departments: [] },
    { name: "Band 10 trở lên", group: "Senior Director / C-level", bands: ["10", "11", "12", "Director / Senior Director / C-level"], departments: [] },
  ],
  cells: [
    [on("PC - Standard"), no(), on(), on(), on(), on(), on(), no()],
    [on("PC - Standard"), no(), on(), on(), on(), on(), on(), no()],
    [on("PC - Performance"), no(), on("", 2), on(), on(), on(), on(), no()],
    [on("PC - Performance"), on("Laptop - Standard"), on("", 2), on(), on(), on(), on(), no()],
    [on("PC - Performance"), on("Laptop - Standard"), on("", 2), on(), on(), on(), on(), no()],
  ],
};

export function addLevel(m: Matrix, level: MatrixLevel): Matrix {
  return { ...m, levels: [...m.levels, level], cells: [...m.cells, m.devices.map(emptyCell)] };
}
export function addDevice(m: Matrix, device: MatrixDevice): Matrix {
  return { ...m, devices: [...m.devices, device], cells: m.cells.map((r) => [...r, emptyCell()]) };
}
export function removeLevel(m: Matrix, i: number): Matrix {
  return { ...m, levels: m.levels.filter((_, j) => j !== i), cells: m.cells.filter((_, j) => j !== i) };
}
export function removeDevice(m: Matrix, i: number): Matrix {
  return { ...m, devices: m.devices.filter((_, j) => j !== i), cells: m.cells.map((r) => r.filter((_, j) => j !== i)) };
}

/**
 * Ma trận cấp phát: rows are band groups, columns are devices, a tick gives that group the device (with how many
 * and which reference configuration). Rows and columns are edited in a dialog from their header.
 */
export function AllocationMatrixEditor({ value, tiers, onChange }: { value: Matrix; tiers: DeviceTier[]; onChange: (m: Matrix) => void }) {
  const [editLevel, setEditLevel] = useState<number | "new" | null>(null);
  const [editDevice, setEditDevice] = useState<number | "new" | null>(null);
  const [presetOpen, setPresetOpen] = useState(false);
  const m = value;
  const setCell = (r: number, c: number, patch: Partial<MatrixCell>) =>
    onChange({ ...m, cells: m.cells.map((row, i) => (i === r ? row.map((x, j) => (j === c ? { ...x, ...patch } : x)) : row)) });
  const overlap = overlappingBands(m.levels);
  const fallback = m.levels.filter((lv) => !lv.bands.length);

  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-md border border-line" tabIndex={0} aria-label="Ma trận cấp phát, cuộn ngang khi hẹp">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Ma trận cấp phát: hàng là cấp bậc, cột là thiết bị</caption>
          <thead>
            <tr className="bg-surface-2">
              <th scope="col" className="sticky left-0 z-10 min-w-56 border-b border-r border-line bg-surface-2 px-3 py-2 text-left font-medium">
                Cấp bậc <span className="font-normal text-muted">/ thiết bị</span>
              </th>
              {m.devices.map((d, c) => (
                <th key={c} scope="col" className="min-w-44 border-b border-line px-3 py-2 text-left align-top font-medium">
                  <button type="button" className="group inline-flex items-center gap-1 rounded-xs text-left hover:text-primary" onClick={() => setEditDevice(c)} aria-label={`Sửa cột ${d.label}`}>
                    {d.label}
                    <Pencil className="size-3.5 text-muted opacity-60 group-hover:opacity-100" aria-hidden="true" />
                  </button>
                  <span className="block text-xs font-normal text-muted">{d.product_types.join(", ")}</span>
                  {d.note && <span className="mt-0.5 line-clamp-2 text-xs font-normal text-pretty text-muted" title={d.note}>{d.note}</span>}
                </th>
              ))}
              <th scope="col" className="w-12 border-b border-line px-2 py-2">
                <Button size="icon" variant="ghost" aria-label="Thêm cột thiết bị" title="Thêm cột thiết bị" onClick={() => setEditDevice("new")}><Plus className="size-4" aria-hidden="true" /></Button>
              </th>
            </tr>
          </thead>
          <tbody>
            {m.levels.map((lv, r) => (
              <tr key={r} className="border-b border-line last:border-b-0">
                <th scope="row" className="sticky left-0 z-10 border-r border-line bg-surface px-3 py-2 text-left align-top font-normal">
                  <button type="button" className="group inline-flex items-center gap-1 rounded-xs text-left font-medium hover:text-primary" onClick={() => setEditLevel(r)} aria-label={`Sửa hàng ${lv.name}`}>
                    {lv.name}
                    <Pencil className="size-3.5 text-muted opacity-60 group-hover:opacity-100" aria-hidden="true" />
                  </button>
                  {lv.group && <span className="block text-xs text-muted">{lv.group}</span>}
                  <span className="block text-xs text-muted">
                    {lv.bands.length ? <>Band RTR: {lv.bands.join(", ")}</> : <>Mọi band chưa có ở hàng khác</>}
                    {lv.departments.length > 0 && <> · chỉ {lv.departments.join(", ")}</>}
                  </span>
                </th>
                {m.devices.map((d, c) => {
                  const cell = m.cells[r]?.[c] ?? emptyCell();
                  const options = tiersFor(d, tiers);
                  return (
                    <td key={c} className={cn("px-3 py-2 align-top transition-colors", cell.on && "bg-primary-soft/60")}>
                      <div className="flex min-h-8 items-center gap-2">
                        <label className="inline-flex cursor-pointer items-center gap-2">
                          <input
                            type="checkbox"
                            className="size-4 accent-primary"
                            checked={cell.on}
                            onChange={(e) => setCell(r, c, { on: e.target.checked })}
                            aria-label={`${lv.name}: cấp ${d.label}`}
                          />
                          <span className={cell.on ? "font-medium" : "text-muted"}>{cell.on ? "Cấp" : "Không"}</span>
                        </label>
                        {cell.on && (
                          <Select
                            aria-label={`${lv.name}: số lượng ${d.label}`}
                            className="ml-auto h-8 w-16 px-2 text-xs tabular"
                            value={cell.quantity}
                            onChange={(e) => setCell(r, c, { quantity: Number(e.target.value) })}
                          >
                            {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>×{n}</option>)}
                          </Select>
                        )}
                      </div>
                      {cell.on && (options.length > 0 || cell.tier) && (
                        <Select aria-label={`${lv.name}: cấu hình ${d.label}`} className="mt-1 h-8 w-full px-2 text-xs" value={cell.tier} onChange={(e) => setCell(r, c, { tier: e.target.value })}>
                          <option value="">Cấu hình bất kỳ</option>
                          {options.map((t) => <option key={t.name} value={t.name}>{t.name}</option>)}
                          {cell.tier && !options.some((t) => t.name === cell.tier) && <option value={cell.tier}>{cell.tier}</option>}
                        </Select>
                      )}
                    </td>
                  );
                })}
                <td />
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button onClick={() => setEditLevel("new")}><Plus className="size-4" aria-hidden="true" />Thêm cấp bậc</Button>
        <Button onClick={() => setEditDevice("new")}><Plus className="size-4" aria-hidden="true" />Thêm thiết bị</Button>
        <Button variant="ghost" className="ml-auto" onClick={() => setPresetOpen(true)}><LayoutGrid className="size-4" aria-hidden="true" />Nạp ma trận mẫu</Button>
      </div>
      <Dialog
        open={presetOpen}
        onClose={() => setPresetOpen(false)}
        size="sm"
        title="Nạp ma trận mẫu?"
        description="Thay ma trận đang soạn bằng mẫu 5 cấp bậc × 8 thiết bị (Intern / Vendor, Band 1–6, Band 4–6 kỹ thuật, Band 7–9, Band 10 trở lên). Sửa lại band cho đúng cách HR ghi trong RTR rồi mới lưu; chưa lưu thì “Hoàn tác thay đổi” vẫn quay lại được."
        footer={
          <>
            <Button onClick={() => setPresetOpen(false)}>Huỷ</Button>
            <Button variant="primary" onClick={() => { onChange(structuredClone(PRESET)); setPresetOpen(false); }}>Nạp mẫu</Button>
          </>
        }
      />
      {(overlap.length > 0 || fallback.length > 1) && (
        <p role="status" className="rounded-sm bg-waiting-soft px-3 py-2 text-sm text-pretty">
          {overlap.length > 0 && <>Band {overlap.join(", ")} nằm ở nhiều hàng – nhân viên band đó nhận thiết bị của tất cả các hàng ấy. </>}
          {fallback.length > 1 && <>Chỉ một hàng được để trống band (hàng “band còn lại”).</>}
        </p>
      )}
      {editLevel !== null && (
        <LevelDialog
          level={editLevel === "new" ? null : m.levels[editLevel]!}
          onClose={() => setEditLevel(null)}
          onSave={(lv) => {
            onChange(editLevel === "new" ? addLevel(m, lv) : { ...m, levels: m.levels.map((x, i) => (i === editLevel ? lv : x)) });
            setEditLevel(null);
          }}
          onRemove={editLevel === "new" ? undefined : () => { onChange(removeLevel(m, editLevel)); setEditLevel(null); }}
        />
      )}
      {editDevice !== null && (
        <DeviceDialog
          device={editDevice === "new" ? null : m.devices[editDevice]!}
          onClose={() => setEditDevice(null)}
          onSave={(d) => {
            onChange(editDevice === "new" ? addDevice(m, d) : { ...m, devices: m.devices.map((x, i) => (i === editDevice ? d : x)) });
            setEditDevice(null);
          }}
          onRemove={editDevice === "new" ? undefined : () => { onChange(removeDevice(m, editDevice)); setEditDevice(null); }}
        />
      )}
    </div>
  );
}

function LevelDialog({ level, onClose, onSave, onRemove }: { level: MatrixLevel | null; onClose: () => void; onSave: (lv: MatrixLevel) => void; onRemove?: () => void }) {
  const [name, setName] = useState(level?.name ?? "");
  const [group, setGroup] = useState(level?.group ?? "");
  const [bands, setBands] = useState((level?.bands ?? []).join(", "));
  const [depts, setDepts] = useState((level?.departments ?? []).join(", "));
  const [touched, setTouched] = useState(false);
  const save = () => { setTouched(true); if (name.trim()) onSave({ name: name.trim(), group: group.trim(), bands: list(bands), departments: list(depts) }); };
  return (
    <Dialog
      open
      onClose={onClose}
      title={level ? `Sửa cấp bậc “${level.name}”` : "Thêm cấp bậc"}
      footer={
        <>
          {onRemove && <Button variant="ghost" className="mr-auto" onClick={onRemove}><Trash2 className="size-4" aria-hidden="true" />Xoá hàng</Button>}
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" onClick={save}>Lưu</Button>
        </>
      }
    >
      <div className="grid gap-4">
        <Field label="Tên cấp bậc" required error="Nhập tên, vd. Band 7–9." showError={touched && !name.trim()}>
          <Input value={name} maxLength={100} onChange={(e) => setName(e.target.value)} placeholder="Band 7–9" />
        </Field>
        <Field label="Nhóm người dùng tham chiếu">
          <Input value={group} maxLength={200} onChange={(e) => setGroup(e.target.value)} placeholder="Manager / Senior Manager / Director" />
        </Field>
        <Field label="Band trong request RTR" hint="Số hoặc chức danh, đúng như HR ghi ở dòng Band của RTR (không phân biệt hoa thường, dấu, khoảng trắng), cách nhau dấu phẩy – vd. 7, 8, 9 hoặc Manager, Senior Manager. Để trống = mọi band chưa có ở hàng khác (vd. Intern / Vendor).">
          <Input value={bands} onChange={(e) => setBands(e.target.value)} placeholder="7, 8, 9 hoặc Manager, Senior Manager" />
        </Field>
        <Field label="Chỉ áp dụng cho phòng ban" hint="Để trống = mọi phòng ban. Có phòng ban thì hàng này thay cho hàng chung cùng band (vd. nhân viên kỹ thuật IT/DATA).">
          <Input value={depts} onChange={(e) => setDepts(e.target.value)} placeholder="IT, DATA" />
        </Field>
      </div>
    </Dialog>
  );
}

function DeviceDialog({ device, onClose, onSave, onRemove }: { device: MatrixDevice | null; onClose: () => void; onSave: (d: MatrixDevice) => void; onRemove?: () => void }) {
  const [d, setD] = useState<Omit<MatrixDevice, "product_types"> & { product_types: string }>({
    label: device?.label ?? "",
    product_types: (device?.product_types ?? []).join(", "),
    product_contains: device?.product_contains ?? "",
    catalog_name: device?.catalog_name ?? "",
    note: device?.note ?? "",
  });
  const [touched, setTouched] = useState(false);
  const types = list(d.product_types);
  const ok = d.label.trim() && types.length > 0;
  const save = () => { setTouched(true); if (ok) onSave({ ...d, label: d.label.trim(), product_types: types }); };
  return (
    <Dialog
      open
      onClose={onClose}
      title={device ? `Sửa thiết bị “${device.label}”` : "Thêm thiết bị"}
      footer={
        <>
          {onRemove && <Button variant="ghost" className="mr-auto" onClick={onRemove}><Trash2 className="size-4" aria-hidden="true" />Xoá cột</Button>}
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" onClick={save}>Lưu</Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Tên thiết bị" required error="Nhập tên, vd. Màn hình." showError={touched && !d.label.trim()}>
          <Input value={d.label} maxLength={100} onChange={(e) => setD({ ...d, label: e.target.value })} placeholder="Màn hình" />
        </Field>
        <Field label="Loại tài sản SDP" required error="Nhập ít nhất một product type." showError={touched && types.length === 0} hint="Product type trên SDP, cách nhau dấu phẩy.">
          <Input value={d.product_types} onChange={(e) => setD({ ...d, product_types: e.target.value })} placeholder="Monitor" />
        </Field>
        <Field label="Model chứa (tuỳ chọn)" hint="Lọc tài sản trong kho theo tên model.">
          <Input value={d.product_contains} maxLength={100} onChange={(e) => setD({ ...d, product_contains: e.target.value })} placeholder='24"' />
        </Field>
        <Field label="Tên trong bảng giá" hint="Điền sẵn khi đề xuất mua (cấu hình có tên riêng thì dùng tên đó).">
          <Input value={d.catalog_name} maxLength={300} onChange={(e) => setD({ ...d, catalog_name: e.target.value })} />
        </Field>
        <Field label="Ghi chú chính sách" className="sm:col-span-2" hint="Hiện dưới tên cột, vd. “Theo nhu cầu công việc – cấp qua Adhoc”.">
          <Input value={d.note} maxLength={300} onChange={(e) => setD({ ...d, note: e.target.value })} />
        </Field>
      </div>
    </Dialog>
  );
}

/** Danh mục cấu hình tham chiếu: the minimum CPU / RAM / storage each tier stands for. */
export function TiersEditor({ value, onChange, used }: { value: DeviceTier[]; onChange: (t: DeviceTier[]) => void; used: Set<string> }) {
  const upd = (i: number, patch: Partial<DeviceTier>) => onChange(value.map((t, j) => (j === i ? { ...t, ...patch } : t)));
  return (
    <div className="space-y-3">
      <div className="overflow-x-auto rounded-md border border-line" tabIndex={0} aria-label="Bảng cấu hình tham chiếu, cuộn ngang khi hẹp">
        <table className="w-full border-collapse text-sm">
          <caption className="sr-only">Cấu hình PC / laptop tham chiếu</caption>
          <thead>
            <tr className="bg-surface-2 text-left">
              {["Cấu hình", "CPU tối thiểu", "RAM", "Ổ cứng", "Mục đích sử dụng", "Tên trong bảng giá"].map((h) => (
                <th key={h} scope="col" className="border-b border-line px-2 py-2 font-medium">{h}</th>
              ))}
              <th scope="col" className="border-b border-line px-2 py-2"><span className="sr-only">Xoá</span></th>
            </tr>
          </thead>
          <tbody>
            {value.map((t, i) => (
              <tr key={i} className="border-b border-line last:border-b-0 align-top">
                <td className="min-w-52 px-2 py-1.5"><Input aria-label={`Tên cấu hình ${i + 1}`} value={t.name} maxLength={100} onChange={(e) => upd(i, { name: e.target.value })} className="font-medium" /></td>
                <td className="min-w-96 px-2 py-1.5"><Input aria-label={`CPU của ${t.name}`} value={t.cpu} maxLength={200} onChange={(e) => upd(i, { cpu: e.target.value })} /></td>
                <td className="w-24 min-w-24 px-2 py-1.5"><Input aria-label={`RAM của ${t.name}`} value={t.ram} maxLength={50} onChange={(e) => upd(i, { ram: e.target.value })} /></td>
                <td className="w-32 min-w-32 px-2 py-1.5"><Input aria-label={`Ổ cứng của ${t.name}`} value={t.storage} maxLength={50} onChange={(e) => upd(i, { storage: e.target.value })} /></td>
                <td className="min-w-80 px-2 py-1.5"><Input aria-label={`Mục đích của ${t.name}`} value={t.description} maxLength={300} onChange={(e) => upd(i, { description: e.target.value })} /></td>
                <td className="min-w-48 px-2 py-1.5"><Input aria-label={`Tên trong bảng giá của ${t.name}`} value={t.catalog_name} maxLength={300} onChange={(e) => upd(i, { catalog_name: e.target.value })} /></td>
                <td className="px-2 py-1.5">
                  <Button
                    size="icon"
                    variant="ghost"
                    disabled={used.has(t.name)}
                    title={used.has(t.name) ? "Đang dùng trong ma trận – bỏ chọn ở ma trận trước" : undefined}
                    aria-label={`Xoá cấu hình ${t.name}`}
                    onClick={() => onChange(value.filter((_, j) => j !== i))}
                  >
                    <Trash2 className="size-4" aria-hidden="true" />
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <Button onClick={() => onChange([...value, { name: "", cpu: "", ram: "", storage: "", description: "", catalog_name: "" }])}>
        <Plus className="size-4" aria-hidden="true" />Thêm cấu hình
      </Button>
    </div>
  );
}
