import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { FileText, Plus, Search, Trash2, X } from "lucide-react";
import { api } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, LoadingRows, Mono } from "@/core/ui/feedback";
import { Field, Input, Select, Textarea } from "@/core/ui/field";
import { formatDateTime } from "@/core/lib/format";
import { useDebounced } from "@/core/lib/useDebounced";
import type { Job, JobAsset, JobStep } from "../jobs";

const CONDITIONS = { good: "Tốt", damaged: "Hỏng", missing_accessory: "Thiếu phụ kiện", not_returned: "Chưa thu hồi" } as const;
type Cond = keyof typeof CONDITIONS;

interface SnapshotAsset {
  id: string;
  name: string;
  asset_tag: string | null;
  serial: string | null;
  product: string | null;
  product_type: string | null;
}

/** Picker for In Store assets from the latest sync (re-checked live when the step runs). */
export function AssetPicker({ open, onClose, selected, onPick }: { open: boolean; onClose: () => void; selected: JobAsset[]; onPick: (a: JobAsset) => void }) {
  const [q, setQ] = useState("");
  const [type, setType] = useState("");
  const debounced = useDebounced(q, 250);
  const list = useQuery({
    queryKey: ["in-store", debounced, type],
    queryFn: () => api<{ items: SnapshotAsset[]; total: number; as_of: string | null; types: string[] }>(
      `/api/lifecycle/assets/in-store?${new URLSearchParams({ q: debounced, product_type: type })}`),
    enabled: open,
  });
  const chosen = new Set(selected.map((a) => a.id));
  return (
    <Dialog open={open} onClose={onClose} title="Chọn tài sản trong kho" size="lg"
      description={list.data?.as_of ? `Theo lần đồng bộ lúc ${formatDateTime(list.data.as_of)}. Trạng thái được kiểm tra lại trên SDP khi chạy bước gán.` : undefined}>
      <div className="mb-3 grid gap-3 sm:grid-cols-[2fr_1fr]">
        <Field label="Tìm tài sản">
          <Input type="search" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Tên, asset tag, serial, model" data-autofocus />
        </Field>
        <Field label="Loại">
          <Select value={type} onChange={(e) => setType(e.target.value)}>
            <option value="">Tất cả</option>
            {list.data?.types.map((t) => <option key={t} value={t}>{t}</option>)}
          </Select>
        </Field>
      </div>
      {list.isLoading ? <LoadingRows rows={4} /> : list.data?.items.length === 0 ? (
        <EmptyState icon={Search} title="Không có tài sản In Store phù hợp">Kiểm tra trạng thái tài sản trên SDP hoặc chạy đồng bộ lại.</EmptyState>
      ) : (
        <table className="w-full text-left">
          <caption className="sr-only">Tài sản trong kho</caption>
          <thead className="text-sm text-muted"><tr><th scope="col" className="py-1.5">Tài sản</th><th scope="col" className="py-1.5">Loại</th><th scope="col" className="py-1.5">Asset tag</th><th scope="col"><span className="sr-only">Chọn</span></th></tr></thead>
          <tbody>
            {list.data?.items.map((a) => (
              <tr key={a.id} className="border-t border-line">
                <td className="py-1.5">{a.name}<span className="block text-xs text-muted">{a.product}</span></td>
                <td className="py-1.5">{a.product_type}</td>
                <td className="py-1.5"><Mono>{a.asset_tag ?? "—"}</Mono></td>
                <td className="py-1.5 text-right">
                  <Button size="sm" disabled={chosen.has(a.id)} onClick={() => onPick({ id: a.id, name: a.name, tag: a.asset_tag, product_type: a.product_type })}>
                    {chosen.has(a.id) ? "Đã chọn" : "Chọn"}
                  </Button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Dialog>
  );
}

/** Draft configuration of an onboard job: SDP user + assets to assign. */
export function DraftConfig({ job, onSave, saving }: { job: Job; onSave: (patch: { assets?: JobAsset[]; sdp_user_id?: string; sdp_user_name?: string; clear_sdp_user?: boolean }) => void; saving: boolean }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [userQ, setUserQ] = useState("");
  const assets = job.params.assets ?? [];
  const users = useQuery({
    queryKey: ["sdp-users", userQ],
    queryFn: () => api<{ id: string; name: string; email: string | null; employee_id: string | null }[]>(`/api/lifecycle/sdp-users?q=${encodeURIComponent(userQ)}`),
    enabled: userQ.trim().length >= 2,
  });
  return (
    <div className="space-y-4">
      <div>
        <h3 className="mb-1 text-base font-semibold">User SDP nhận tài sản</h3>
        {job.params.sdp_user_id ? (
          <p className="flex flex-wrap items-center gap-2">
            {job.params.sdp_user_name ?? job.params.sdp_user_id}
            <Button size="sm" variant="ghost" onClick={() => onSave({ clear_sdp_user: true })} disabled={saving}>Bỏ chọn (tự tìm theo Mã NV khi chạy)</Button>
          </p>
        ) : (
          <>
            <p className="mb-2 text-sm text-muted">Chưa chọn: khi chạy, infra-ops tìm user SDP theo Mã NV {job.subject_key} và chờ nếu tài khoản chưa được đồng bộ vào SDP.</p>
            <Field label="Chọn user SDP khác (tuỳ chọn)" className="max-w-md">
              <Input type="search" value={userQ} onChange={(e) => setUserQ(e.target.value)} placeholder="Gõ ít nhất 2 ký tự" />
            </Field>
            {users.data && users.data.length > 0 && (
              <ul className="mt-1 max-w-md divide-y divide-line rounded-sm border border-line">
                {users.data.map((u) => (
                  <li key={u.id} className="flex items-center justify-between gap-2 px-2 py-1.5 text-sm">
                    <span>{u.name} <span className="text-muted">{u.email} {u.employee_id && `· ${u.employee_id}`}</span></span>
                    <Button size="sm" onClick={() => onSave({ sdp_user_id: u.id, sdp_user_name: u.name })}>Chọn</Button>
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>
      <div>
        <div className="mb-1 flex items-center justify-between gap-2">
          <h3 className="text-base font-semibold">Tài sản bàn giao ({assets.length})</h3>
          <Button size="sm" onClick={() => setPickerOpen(true)}><Plus className="size-4" aria-hidden="true" />Chọn từ kho</Button>
        </div>
        {assets.length === 0 ? (
          <p className="text-sm text-muted">Chưa chọn tài sản. Bước “Gán tài sản” sẽ không thay đổi gì nếu để trống.</p>
        ) : (
          <ul className="divide-y divide-line rounded-sm border border-line">
            {assets.map((a) => (
              <li key={a.id} className="flex items-center justify-between gap-2 px-3 py-1.5">
                <span>{a.name} <span className="text-sm text-muted">{a.product_type} · <Mono>{a.tag ?? "—"}</Mono></span></span>
                <Button size="icon" variant="ghost" aria-label={`Bỏ ${a.name}`} onClick={() => onSave({ assets: assets.filter((x) => x.id !== a.id) })}>
                  <Trash2 className="size-4" aria-hidden="true" />
                </Button>
              </li>
            ))}
          </ul>
        )}
      </div>
      <AssetPicker open={pickerOpen} onClose={() => setPickerOpen(false)} selected={assets} onPick={(a) => onSave({ assets: [...assets, a] })} />
    </div>
  );
}

/** Offboard: confirm the condition of each asset received back. */
export function CollectAssetsForm({ step, onSubmit, busy }: { step: JobStep; onSubmit: (data: Record<string, unknown>) => void; busy: boolean }) {
  const listed = (step.output.assets as { id: string; name: string; tag: string | null; product_type: string | null }[] | undefined) ?? [];
  const [rows, setRows] = useState(() => listed.map((a) => ({ id: a.id, name: a.name, tag: a.tag ?? "", condition: "" as Cond | "", note: "", manual: false })));
  const [submitted, setSubmitted] = useState(false);
  const missing = rows.some((r) => !r.condition || (r.manual && !r.name.trim()));
  const update = (i: number, patch: Partial<(typeof rows)[number]>) => setRows((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        setSubmitted(true);
        if (!missing) onSubmit({ assets: rows.map(({ id, name, tag, condition, note }) => ({ id, name, tag, condition, note })) });
      }}
      noValidate
    >
      <p className="mb-2 text-sm text-muted">Danh sách theo {String(step.output.source ?? "SDP")}. Thiết bị nhân viên chưa trả: chọn “Chưa thu hồi” – vẫn in trên biên bản và không chuyển về kho.</p>
      <div className="overflow-x-auto">
        <table className="w-full text-left">
          <caption className="sr-only">Xác nhận thu hồi tài sản</caption>
          <thead className="text-sm text-muted"><tr><th scope="col" className="py-1.5">Tài sản</th><th scope="col" className="py-1.5">Tình trạng</th><th scope="col" className="py-1.5">Ghi chú</th><th scope="col"><span className="sr-only">Xoá</span></th></tr></thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.id} className="border-t border-line align-top">
                <td className="py-1.5 pr-2">
                  {r.manual ? (
                    <div className="flex gap-2">
                      <Input aria-label={`Tên thiết bị thêm tay dòng ${i + 1}`} value={r.name} onChange={(e) => update(i, { name: e.target.value })} placeholder="Tên thiết bị" />
                      <Input aria-label={`Asset tag dòng ${i + 1}`} value={r.tag} onChange={(e) => update(i, { tag: e.target.value })} placeholder="Asset tag" className="w-32 font-mono" />
                    </div>
                  ) : (
                    <>{r.name} <Mono className="text-muted">{r.tag || "—"}</Mono></>
                  )}
                </td>
                <td className="py-1.5 pr-2">
                  <Select aria-label={`Tình trạng ${r.name || `dòng ${i + 1}`}`} aria-invalid={submitted && !r.condition ? "true" : "false"} value={r.condition} onChange={(e) => update(i, { condition: e.target.value as Cond })}>
                    <option value="">Chọn tình trạng</option>
                    {Object.entries(CONDITIONS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
                  </Select>
                </td>
                <td className="py-1.5 pr-2"><Input aria-label={`Ghi chú ${r.name || `dòng ${i + 1}`}`} value={r.note} onChange={(e) => update(i, { note: e.target.value })} /></td>
                <td className="py-1.5">
                  {r.manual && (
                    <Button size="icon" variant="ghost" aria-label={`Xoá dòng ${i + 1}`} onClick={() => setRows((rs) => rs.filter((_, j) => j !== i))}>
                      <X className="size-4" aria-hidden="true" />
                    </Button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {rows.length === 0 && <p className="py-2 text-sm text-muted">SDP không ghi nhận tài sản nào đang gán. Thêm tay nếu nhân viên trả thiết bị khác.</p>}
      {submitted && missing && <p role="alert" className="mt-2 text-sm text-failed">Chọn tình trạng (và nhập tên với thiết bị thêm tay) cho mọi dòng.</p>}
      <div className="mt-3 flex flex-wrap gap-2">
        <Button onClick={() => setRows((rs) => [...rs, { id: `manual-${Date.now()}`, name: "", tag: "", condition: "", note: "", manual: true }])}>
          <Plus className="size-4" aria-hidden="true" />Thêm thiết bị khác
        </Button>
        <Button type="submit" variant="primary" loading={busy}>Xác nhận thu hồi</Button>
      </div>
    </form>
  );
}

/** Onboard: card/fingerprint/face enrolled at the device; the server verifies on BioStar. */
export function EnrollForm({ onSubmit, busy }: { onSubmit: (data: Record<string, unknown>) => void; busy: boolean }) {
  const [force, setForce] = useState(false);
  const [note, setNote] = useState("");
  return (
    <form onSubmit={(e) => { e.preventDefault(); onSubmit({ force, note }); }} className="space-y-3">
      <p className="text-sm">Hướng dẫn nhân viên đăng ký thẻ, vân tay hoặc khuôn mặt tại thiết bị BioStation 3. Khi bấm xác nhận, infra-ops kiểm tra lại trên BioStar.</p>
      <label className="flex cursor-pointer items-start gap-2 text-sm">
        <input type="checkbox" className="mt-0.5 size-4 accent-[var(--primary)]" checked={force} onChange={(e) => setForce(e.target.checked)} />
        <span>Xác nhận dù BioStar chưa ghi nhận (ví dụ nhân viên dùng mã PIN) – cần ghi chú lý do</span>
      </label>
      {force && (
        <Field label="Lý do" required>
          <Textarea rows={2} value={note} onChange={(e) => setNote(e.target.value)} maxLength={300} />
        </Field>
      )}
      <Button type="submit" variant="primary" loading={busy} disabled={force && note.trim().length < 3}>Kiểm tra và xác nhận</Button>
    </form>
  );
}

export function DocumentLink({ step }: { step: JobStep }) {
  const id = step.output.document_id as string | undefined;
  if (!id) return null;
  return (
    <Link to={`/lifecycle/documents/${id}`} className="inline-flex h-8 items-center gap-1.5 rounded-sm bg-primary px-3 font-medium text-on-primary hover:bg-primary-hover">
      <FileText className="size-4" aria-hidden="true" />
      Mở biên bản {String(step.output.number ?? "")}
    </Link>
  );
}
