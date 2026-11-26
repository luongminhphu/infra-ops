import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MapPin, Plus } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { ErrorState, LoadingRows, Mono, Panel } from "@/core/ui/feedback";
import { Field, Input, Switch, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import { parseIds } from "./AttendancePage";

export interface Area {
  id: string;
  name: string;
  is_store: boolean;
  departments: string[];
  employee_ids: string[];
  position: number;
  assets: number;
}

interface Areas {
  areas: Area[];
  departments: { name: string; assets: number }[];
  uncovered: { employee_id: string | null; name: string; department: string | null; assets: number }[];
  uncovered_total: number;
  unused_without_store: number;
}

export const AREAS_KEY = ["lifecycle", "stocktake-areas"];

/** Departments another area already has (each department belongs to one area). */
export function takenDepartments(areas: Area[], editing: string | null): Map<string, string> {
  const out = new Map<string, string>();
  for (const a of areas) if (a.id !== editing) for (const d of a.departments) out.set(d.toLowerCase(), a.name);
  return out;
}

/** Khu kiểm kê: floors made of SDP departments plus employee IDs typed in, and Kho IT for unused assets. */
export function AreasPanel() {
  const { can } = useAuth();
  const manage = can("lifecycle.stocktake.manage");
  const q = useQuery({ queryKey: AREAS_KEY, queryFn: () => api<Areas>("/api/lifecycle/stocktake-areas") });
  const [editing, setEditing] = useState<Area | "new" | null>(null);
  const [showUncovered, setShowUncovered] = useState(false);
  if (q.isLoading) return <LoadingRows rows={3} />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const d = q.data;
  return (
    <Panel
      className="mt-6"
      title={
        <span className="inline-flex items-center gap-1.5">
          Khu kiểm kê
          <HelpTip title="Khu kiểm kê">
            <span className="block">Mỗi khu (thường là một tầng) gồm nhân viên của các phòng ban SDP đã chọn, cộng Mã NV gõ thêm – Mã NV gõ thêm được ưu tiên khi một phòng ban ngồi nhiều tầng.</span>
            <span className="block">Tài sản thuộc khu của người đang dùng. Tài sản không có người dùng vào khu kho (Kho IT). Danh sách khu dùng lại năm sau; kỳ đã mở giữ nguyên khu lúc mở.</span>
          </HelpTip>
        </span>
      }
      actions={manage && <Button size="sm" onClick={() => setEditing("new")}><Plus className="size-4" aria-hidden="true" />Thêm khu</Button>}
      bodyClassName="p-0"
    >
      {(d.uncovered_total > 0 || d.unused_without_store > 0) && (
        <div role="status" className="m-4 rounded-md border border-waiting bg-waiting-soft px-3 py-2 text-sm">
          <p className="flex flex-wrap items-center gap-2">
            <StatusBadge status="warning" label="Chưa phân khu" />
            {d.uncovered_total > 0 && <span>{d.uncovered_total} nhân viên đang giữ tài sản chưa thuộc khu nào.</span>}
            {d.unused_without_store > 0 && <span>{d.unused_without_store} tài sản không có người dùng – chưa có khu kho.</span>}
            {d.uncovered_total > 0 && <Button size="sm" variant="link" onClick={() => setShowUncovered(!showUncovered)}>{showUncovered ? "Ẩn danh sách" : "Xem danh sách"}</Button>}
          </p>
          {showUncovered && (
            <ul className="mt-2 max-h-64 overflow-y-auto" tabIndex={0} aria-label="Nhân viên chưa thuộc khu nào">
              {d.uncovered.map((u) => (
                <li key={`${u.employee_id}-${u.name}`} className="py-0.5"><Mono>{u.employee_id ?? "—"}</Mono> · {u.name} <span className="text-muted">· {u.department ?? "không có phòng ban"} · {u.assets} tài sản</span></li>
              ))}
            </ul>
          )}
        </div>
      )}
      {d.areas.length === 0 ? (
        <p className="px-4 py-4 text-sm text-muted">Chưa có khu. Thêm từng tầng (chọn phòng ban, gõ thêm Mã NV) và một khu kho “Kho IT”.</p>
      ) : (
        <ul className="divide-y divide-line border-t border-line">
          {d.areas.map((a) => (
            <li key={a.id} className="flex flex-wrap items-start gap-x-4 gap-y-1 px-4 py-3">
              <div className="min-w-0 flex-1">
                <p className="flex flex-wrap items-center gap-2 font-medium">
                  <MapPin className="size-4 text-muted" aria-hidden="true" />{a.name}
                  {a.is_store && <StatusBadge status="pending" label="Tài sản không có người dùng" />}
                </p>
                <p className="mt-0.5 text-sm text-muted text-pretty">
                  {a.departments.length > 0 && <>Phòng ban: {a.departments.join(", ")}</>}
                  {a.departments.length > 0 && a.employee_ids.length > 0 && " · "}
                  {a.employee_ids.length > 0 && <>{a.employee_ids.length} Mã NV gõ thêm</>}
                </p>
              </div>
              <span className="text-sm tabular text-muted">{a.assets} tài sản</span>
              {manage && <Button size="sm" variant="ghost" onClick={() => setEditing(a)}>Sửa</Button>}
            </li>
          ))}
        </ul>
      )}
      {editing && <AreaDialog area={editing === "new" ? null : editing} data={d} onClose={() => setEditing(null)} />}
    </Panel>
  );
}

function AreaDialog({ area, data, onClose }: { area: Area | null; data: Areas; onClose: () => void }) {
  const qc = useQueryClient();
  const [name, setName] = useState(area?.name ?? "");
  const [isStore, setIsStore] = useState(area?.is_store ?? false);
  const [depts, setDepts] = useState<string[]>(area?.departments ?? []);
  const [ids, setIds] = useState((area?.employee_ids ?? []).join("\n"));
  const [position, setPosition] = useState(area?.position ?? data.areas.length + 1);
  const [filter, setFilter] = useState("");
  const [touched, setTouched] = useState(false);
  const taken = takenDepartments(data.areas, area?.id ?? null);
  const list = parseIds(ids);
  const done = async (msg: string) => {
    toast.success(msg);
    await qc.invalidateQueries({ queryKey: AREAS_KEY });
    onClose();
  };
  const save = useMutation({
    mutationFn: () =>
      api(area ? `/api/lifecycle/stocktake-areas/${area.id}` : "/api/lifecycle/stocktake-areas", {
        method: area ? "PUT" : "POST",
        json: { name, is_store: isStore, departments: depts, employee_ids: list, position },
      }),
    onSuccess: () => done(area ? "Đã lưu khu." : "Đã thêm khu."),
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/lifecycle/stocktake-areas/${area!.id}`, { method: "DELETE" }),
    onSuccess: () => done("Đã xoá khu."),
    onError: (e) => toast.error(errorMessage(e)),
  });
  // Departments still free or already in this area, then the rest (shown with the area that has them).
  const shown = data.departments.filter((x) => x.name.toLowerCase().includes(filter.trim().toLowerCase()));
  const toggle = (d: string) => setDepts(depts.includes(d) ? depts.filter((x) => x !== d) : [...depts, d]);
  return (
    <Dialog
      open
      size="lg"
      onClose={onClose}
      title={area ? `Sửa khu “${area.name}”` : "Thêm khu kiểm kê"}
      footer={
        <>
          {area && <Button variant="ghost" className="mr-auto" loading={remove.isPending} onClick={() => remove.mutate()}>Xoá khu</Button>}
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" loading={save.isPending} onClick={() => { setTouched(true); if (name.trim()) save.mutate(); }}>Lưu</Button>
        </>
      }
    >
      <div className="grid gap-4">
        <div className="flex flex-wrap items-start gap-4">
          <Field label="Tên khu" required error="Nhập tên khu, vd. Tầng 5." showError={touched && !name.trim()} className="min-w-56 flex-1">
            <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={100} placeholder="vd. Tầng 5" />
          </Field>
          <Field label="Thứ tự" className="w-24">
            <Input type="number" min={0} max={1000} value={position} onChange={(e) => setPosition(Number(e.target.value) || 0)} />
          </Field>
        </div>
        <Switch label="Khu kho (Kho IT)" description="Nhận mọi tài sản không có người dùng: In Store, chờ thanh lý. Chỉ một khu kho." checked={isStore} onChange={setIsStore} />
        <fieldset className="grid gap-2">
          <legend className="mb-1 text-sm font-medium">Phòng ban SDP <span className="font-normal text-muted">({depts.length} đã chọn)</span></legend>
          <Input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder="Lọc phòng ban" aria-label="Lọc phòng ban" />
          <ul className="max-h-56 overflow-y-auto rounded-md border border-line px-3 py-1" aria-label="Phòng ban">
            {shown.map((x) => {
              const other = taken.get(x.name.toLowerCase());
              return (
                <li key={x.name}>
                  <label className="flex min-h-9 items-center gap-2 text-sm">
                    <input type="checkbox" checked={depts.includes(x.name)} disabled={Boolean(other)} onChange={() => toggle(x.name)} />
                    <span className="flex-1">{x.name}</span>
                    <span className="text-muted tabular">{other ? `thuộc ${other}` : `${x.assets} tài sản`}</span>
                  </label>
                </li>
              );
            })}
            {shown.length === 0 && <li className="py-2 text-sm text-muted">Không có phòng ban (chạy Đồng bộ dữ liệu để lấy từ SDP).</li>}
          </ul>
        </fieldset>
        <Field label="Mã NV gõ thêm" hint={`Người ngồi khu này dù phòng ban ở nơi khác – được ưu tiên hơn phòng ban. Đang có ${list.length} Mã NV.`}>
          <Textarea rows={4} value={ids} onChange={(e) => setIds(e.target.value)} placeholder="003551, 003475…" className="font-mono placeholder:font-sans" />
        </Field>
      </div>
    </Dialog>
  );
}
