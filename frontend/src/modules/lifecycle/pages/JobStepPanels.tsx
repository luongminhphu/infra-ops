import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, Ban, Check, Copy, FileText, Minus, Plus, RefreshCw, Search, ShoppingCart, Trash2, X } from "lucide-react";
import { api } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, LoadingRows, Mono } from "@/core/ui/feedback";
import { From } from "@/core/ui/wizard";
import { Field, Input, Select, Switch, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { useDebounced } from "@/core/lib/useDebounced";
import { buyLink } from "../buy";
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
export interface ParamsPatch { assets?: JobAsset[]; sdp_user_id?: string; sdp_user_name?: string; clear_sdp_user?: boolean; mailbox_handover?: boolean }

/** Offboard "keep the mailbox" switch; only for jobs that have the Microsoft steps. Warns when it is on but no retained OU is configured. */
export function MailboxOption({ job, onSave, saving }: { job: Job; onSave: (patch: ParamsPatch) => void; saving: boolean }) {
  const on = !!job.params.options?.mailbox_handover;
  const prep = useQuery({
    queryKey: ["job-prepare", job.id],
    queryFn: () => api<{ retained_ou_configured: boolean }>(`/api/lifecycle/jobs/${job.id}/prepare`),
    enabled: on && !!job.params.microsoft,
  });
  if (!job.params.microsoft) return <p className="text-sm text-muted">Job này không có bước Microsoft nên không có tuỳ chọn giữ mailbox.</p>;
  return (
    <div className="space-y-2">
      <div className="flex items-start gap-1.5">
        <Switch checked={on} disabled={saving} onChange={(v) => onSave({ mailbox_handover: v })} label="Giữ mailbox (shared/forward) theo yêu cầu quản lý" />
        <HelpTip title="Giữ mailbox">
          Tài khoản được chuyển vào OU giữ mailbox; hết hạn giữ, app tạo việc chuyển vào Ban_Users. Đổi được tới khi bước chuyển vào Ban_Users được xác nhận.
        </HelpTip>
      </div>
      {on && prep.data?.retained_ou_configured === false && (
        <Callout>Chưa cấu hình OU giữ mailbox (ad_retained_ou) trong Cấu hình module – bước chuyển vào Ban_Users sẽ lỗi. Báo quản lý cấu hình trước khi chạy.</Callout>
      )}
    </div>
  );
}

export function DraftConfig({ job, onSave, saving }: { job: Job; onSave: (patch: ParamsPatch) => void; saving: boolean }) {
  const [pickerOpen, setPickerOpen] = useState(false);
  const [userQ, setUserQ] = useState("");
  const debouncedUserQ = useDebounced(userQ, 250);
  const assets = job.params.assets ?? [];
  const users = useQuery({
    queryKey: ["sdp-users", debouncedUserQ],
    queryFn: () => api<{ id: string; name: string; email: string | null; employee_id: string | null }[]>(`/api/lifecycle/sdp-users?q=${encodeURIComponent(debouncedUserQ)}`),
    enabled: debouncedUserQ.trim().length >= 2,
  });
  if (job.params.kind === "offboard") return <MailboxOption job={job} onSave={onSave} saving={saving} />;
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

interface Candidate { sam: string; upn: string; free: boolean }
const CUSTOM = "__custom__";

function Callout({ children }: { children: string }) {
  return (
    <p role="status" className="flex items-start gap-1.5 rounded-sm border border-waiting/40 bg-waiting-soft px-3 py-2 text-sm">
      <AlertTriangle className="mt-0.5 size-4 shrink-0 text-waiting" aria-hidden="true" />{children}
    </p>
  );
}

/** Re-reads the systems for a plan that is blocked or incomplete (the worker runs the step's prepare again). */
function ReprepareButton({ onReprepare, busy }: { onReprepare: () => void; busy?: boolean }) {
  return <Button type="button" loading={busy} onClick={onReprepare}><RefreshCw className="size-4" aria-hidden="true" />Chuẩn bị lại</Button>;
}

/** Onboard `account_plan`: confirm the AD username / OU the system proposes. Custom values need the assign permission. */
export function AccountPlanForm({ step, onSubmit, busy, onReprepare, reprepareBusy }: { step: JobStep; onSubmit: (data: Record<string, unknown>) => void; busy: boolean; onReprepare?: () => void; reprepareBusy?: boolean }) {
  const { can } = useAuth();
  const o = step.output as { level?: string | null; employee_id?: string; cn?: string; ou?: string; ou_rule?: string | null; ou_options?: string[]; manager_dn?: string | null; candidates?: Candidate[]; proposed_sam?: string | null; sam_max_length?: number; warnings?: string[]; software?: string[]; license_names?: string[] };
  const candidates = o.candidates ?? [];
  const proposed = o.proposed_sam ?? "";
  const planOu = o.ou ?? "";
  const mayCustom = can("lifecycle.job.assign");
  const noProposal = !proposed && !mayCustom;
  const [choice, setChoice] = useState(proposed);
  const [custom, setCustom] = useState("");
  const [ou, setOu] = useState(planOu);
  // OU choices come with the plan (configured OUs, Ban_Users excluded) – no settings permission needed.
  const ous = [...new Set([planOu, ...(o.ou_options ?? [])].filter((x): x is string => !!x))];
  const sam = (choice === CUSTOM ? custom : choice).trim().toLowerCase();
  const source = `Ma trận cấp phát – hàng ${o.level ?? "—"}`;
  const warnings = o.warnings ?? [];
  const software = o.software ?? [];
  const licenses = o.license_names ?? [];
  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        if (sam) onSubmit({ sam, ...(ou && ou !== planOu ? { ou } : {}) });
      }}
      className="space-y-4"
      noValidate
    >
      {warnings.map((w) => <Callout key={w}>{w}</Callout>)}
      {noProposal && <Callout>Hệ thống chưa đề xuất được username (mọi phương án đã dùng hoặc chưa đủ dữ liệu). Nhờ người có quyền “Chuyển Job” (quản lý/giám sát) chọn username.</Callout>}
      <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1.5 text-sm">
        <dt className="text-muted">Cấp bậc</dt><dd>{o.level ?? "—"}</dd>
        <dt className="text-muted">License</dt><dd>{licenses.join(", ") || "—"} <From>{source}</From></dd>
        <dt className="text-muted">Phần mềm</dt><dd>{software.join(", ") || "—"} <From>{source}</From></dd>
        <dt className="text-muted">employeeID</dt><dd><Mono>{o.employee_id}</Mono></dd>
        <dt className="text-muted">CN</dt><dd>{o.cn}</dd>
        <dt className="text-muted">OU</dt><dd className="break-all"><Mono>{ou || "—"}</Mono>{o.ou_rule && <From>{`quy tắc ${o.ou_rule}`}</From>}</dd>
        <dt className="text-muted">Quản lý (manager)</dt><dd className="break-all">{o.manager_dn || "—"}</dd>
      </dl>
      <fieldset className="space-y-1.5">
        <legend className="mb-1 text-base font-semibold">Username</legend>
        {candidates.map((c) => (
          <label key={c.sam} className={`flex items-center gap-2 text-sm ${c.free ? "cursor-pointer" : "text-muted"}`}>
            <input type="radio" name="sam" className="size-4 accent-[var(--primary)]" value={c.sam} disabled={!c.free} checked={choice === c.sam} onChange={() => setChoice(c.sam)} />
            <Mono>{c.sam}</Mono><span className="text-muted">{c.upn}</span>
            {!c.free && <span className="inline-flex items-center gap-1 rounded-xs bg-surface-2 px-1.5 py-0.5 text-xs"><Ban className="size-3" aria-hidden="true" />đã dùng</span>}
            {c.sam === proposed && <From>đề xuất</From>}
          </label>
        ))}
        {mayCustom && (
          <>
            <label className="flex cursor-pointer items-center gap-2 text-sm">
              <input type="radio" name="sam" className="size-4 accent-[var(--primary)]" value={CUSTOM} checked={choice === CUSTOM} onChange={() => setChoice(CUSTOM)} />
              Nhập username khác
            </label>
            {choice === CUSTOM && (
              <Field label="Username" className="max-w-xs"><Input value={custom} onChange={(e) => setCustom(e.target.value)} maxLength={o.sam_max_length ?? 20} className="font-mono" /></Field>
            )}
            <Field label="OU tạo tài khoản" className="max-w-xl">
              <Select value={ou} onChange={(e) => setOu(e.target.value)}>
                {ous.map((x) => <option key={x} value={x}>{x}</option>)}
              </Select>
            </Field>
          </>
        )}
      </fieldset>
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" loading={busy} disabled={!sam}>Chốt tài khoản này</Button>
        {onReprepare && (warnings.length > 0 || !proposed) && <ReprepareButton onReprepare={onReprepare} busy={reprepareBusy} />}
      </div>
    </form>
  );
}

/** Onboard `it_handover`: per software item, installed or not needed (with a reason). */
export function ItHandoverForm({ step, onSubmit, busy }: { step: JobStep; onSubmit: (data: Record<string, unknown>) => void; busy: boolean }) {
  const software = (step.output.software as string[] | undefined) ?? [];
  const [rows, setRows] = useState<Record<string, { state: "installed" | "not_needed" | ""; note: string }>>(() => Object.fromEntries(software.map((n) => [n, { state: "", note: "" }])));
  const [tried, setTried] = useState(false);
  const bad = software.filter((n) => !rows[n]!.state || (rows[n]!.state === "not_needed" && !rows[n]!.note.trim()));
  const set = (n: string, patch: Partial<{ state: "installed" | "not_needed" | ""; note: string }>) => setRows((r) => ({ ...r, [n]: { ...r[n]!, ...patch } }));
  return (
    <form
      noValidate
      className="space-y-3"
      onSubmit={(e) => {
        e.preventDefault();
        setTried(true);
        if (bad.length) return;
        onSubmit({
          installed: software.filter((n) => rows[n]!.state === "installed"),
          not_needed: software.filter((n) => rows[n]!.state === "not_needed").map((n) => ({ name: n, note: rows[n]!.note.trim() })),
        });
      }}
    >
      <p className="text-sm text-muted">Mật khẩu tạm đặt trên ADUC bởi người bàn giao – app không nhận và không lưu mật khẩu.</p>
      {software.length === 0 && <p className="text-sm text-muted">Không có phần mềm cần cài theo ma trận cấp phát.</p>}
      <ul className="divide-y divide-line rounded-sm border border-line">
        {software.map((n) => (
          <li key={n} className="flex flex-wrap items-center gap-3 px-3 py-2 text-sm">
            <span className="min-w-40 flex-1 font-medium">{n}</span>
            {(["installed", "not_needed"] as const).map((v) => (
              <label key={v} className="flex cursor-pointer items-center gap-1.5">
                <input type="radio" name={`sw-${n}`} className="size-4 accent-[var(--primary)]" checked={rows[n]!.state === v} onChange={() => set(n, { state: v })} />
                {v === "installed" ? "Đã cài" : "Không cần"}
              </label>
            ))}
            {rows[n]!.state === "not_needed" && (
              <Input aria-label={`Lý do không cần ${n}`} aria-invalid={tried && !rows[n]!.note.trim() ? "true" : "false"} placeholder="Lý do" value={rows[n]!.note} onChange={(e) => set(n, { note: e.target.value })} className="w-56" />
            )}
          </li>
        ))}
      </ul>
      {tried && bad.length > 0 && <p role="alert" className="text-sm text-failed">Chọn “Đã cài” hoặc “Không cần” (kèm lý do) cho: {bad.join(", ")}.</p>}
      <Button type="submit" variant="primary" loading={busy}>Xác nhận bàn giao máy</Button>
    </form>
  );
}

/** `rtr_checklist`: the text HR copies into RTR. */
export function RtrChecklistPanel({ step }: { step: JobStep }) {
  const [copied, setCopied] = useState(false);
  const summary = String(step.output.summary ?? "");
  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-base font-semibold">Tóm tắt để dán vào RTR</h3>
        <Button
          size="sm"
          disabled={!summary}
          onClick={() => void navigator.clipboard.writeText(summary).then(() => { setCopied(true); setTimeout(() => setCopied(false), 2000); })}
        >
          {copied ? <Check className="size-4" aria-hidden="true" /> : <Copy className="size-4" aria-hidden="true" />}
          {copied ? "Đã sao chép" : "Sao chép"}
        </Button>
      </div>
      <pre aria-label="Tóm tắt RTR" className="whitespace-pre-wrap rounded-sm border border-line bg-surface-2 p-3 font-mono text-sm">{summary || "Chưa có nội dung."}</pre>
    </div>
  );
}

/** Offboard `mailbox_handover`: guidance and "Hoàn tất" when requested; when the option is off only "Bỏ qua bước" applies (completing would stall verification). */
export function MailboxHandoverPanel({ job, step, busy, onComplete }: { job: Job; step: JobStep; busy: boolean; onComplete: () => void }) {
  if (!job.params.options?.mailbox_handover) {
    return <p className="rounded-sm bg-surface-2 px-3 py-2 text-sm">Không yêu cầu – bấm Bỏ qua bước.</p>;
  }
  return (
    <>
      <p className="rounded-sm bg-surface-2 px-3 py-2 text-sm">{step.message}</p>
      <Button variant="primary" loading={busy} onClick={onComplete}>Đánh dấu hoàn tất</Button>
    </>
  );
}

const SOURCE_LABEL: Record<string, string> = { request: "Request mới", request_old: "Request cũ", ad: "AD hiện tại" };

interface Lines { add?: string[]; remove?: string[]; names?: Record<string, string>; level?: string; level_why?: string }

/** Checkable removal lines (pinned by the person; unchecking needs the assign permission). */
function RemoveLines({ id, title, items, label, kept, setKept, locked }: { id: string; title: string; items: string[]; label: (x: string) => string; kept: string[]; setKept: (v: string[]) => void; locked: boolean }) {
  if (items.length === 0) return null;
  return (
    <fieldset className="space-y-1.5" aria-labelledby={id}>
      <legend id={id} className="mb-1 text-base font-semibold">{title} ({items.length})</legend>
      {items.map((g) => (
        <label key={g} className="flex items-start gap-2 text-sm">
          <input
            type="checkbox"
            className="mt-0.5 size-4 accent-[var(--primary)]"
            checked={kept.includes(g)}
            disabled={locked}
            onChange={(e) => setKept(e.target.checked ? [...kept, g] : kept.filter((x) => x !== g))}
          />
          <span className="break-all">{label(g)}</span>
        </label>
      ))}
    </fieldset>
  );
}

/** Transfer `transfer_plan`: before -> after, groups / licenses / BioStar lines, then pin what the person confirms. Removals / OU change need the assign permission. */
export function TransferPlanForm({ step, onSubmit, busy, onReprepare, reprepareBusy }: { step: JobStep; onSubmit: (data: Record<string, unknown>) => void; busy: boolean; onReprepare?: () => void; reprepareBusy?: boolean }) {
  const { can } = useAuth();
  const o = step.output as {
    blocked?: boolean; ou?: string; target_ou?: string | null; ou_options?: string[]; add?: string[]; remove?: string[];
    attributes?: Record<string, string>; current?: Record<string, string>; effective_date?: string | null;
    sources?: Record<string, string>; warnings?: string[]; licenses?: Lines; biostar?: Lines;
  };
  const mayEdit = can("lifecycle.job.assign");
  const remove = o.remove ?? [];
  const [kept, setKept] = useState<string[]>(remove);
  const lic = o.licenses ?? {};
  const bio = o.biostar ?? {};
  const [keptLic, setKeptLic] = useState<string[]>(lic.remove ?? []);
  const [keptBio, setKeptBio] = useState<string[]>(bio.remove ?? []);
  const [eff, setEff] = useState("");
  const needsDate = !o.effective_date;
  const planOu = o.target_ou ?? "";
  const [ou, setOu] = useState(planOu);
  const ous = [...new Set([planOu, ...(o.ou_options ?? [])].filter((x) => !!x))];
  const org = SOURCE_LABEL[o.sources?.org ?? "request"] ?? "Request mới";
  const cur = o.current ?? {};
  const attrs = o.attributes ?? {};
  const skuName = (x: string) => lic.names?.[x] ?? x;
  const rows: { label: string; before: string; after: string; from: string }[] = [
    { label: "OU", before: o.ou ?? "", after: planOu, from: org },
    { label: "Chức danh", before: cur.title ?? "", after: attrs.title ?? "", from: SOURCE_LABEL.request! },
    { label: "Phòng ban", before: cur.department ?? "", after: attrs.department ?? "", from: org },
    { label: "Quản lý (manager)", before: cur.manager ?? "", after: attrs.manager_employee_id ? `Mã NV ${attrs.manager_employee_id}` : attrs.manager ?? "", from: SOURCE_LABEL.request! },
  ];
  const addLines = (id: string, title: string, items: string[], label: (x: string) => string) => items.length > 0 && (
    <section aria-labelledby={id} className="space-y-1">
      <h3 id={id} className="text-base font-semibold">{title} ({items.length})</h3>
      <ul className="space-y-1 text-sm">{items.map((g) => <li key={g} className="flex items-center gap-1.5 break-all"><Plus className="size-4 shrink-0 text-done" aria-hidden="true" />{label(g)}</li>)}</ul>
    </section>
  );
  return (
    <form
      noValidate
      className="space-y-4"
      onSubmit={(e) => {
        e.preventDefault();
        if (o.blocked || (needsDate && !eff)) return;
        onSubmit({
          remove: kept,
          ...((lic.remove ?? []).length ? { remove_licenses: keptLic } : {}),
          ...((bio.remove ?? []).length ? { remove_biostar: keptBio } : {}),
          ...(ou && ou !== planOu ? { ou } : {}),
          ...(needsDate ? { effective_date: eff } : {}),
        });
      }}
    >
      {(o.warnings ?? []).map((w) => <Callout key={w}>{w}</Callout>)}
      {needsDate ? (
        <div className="rounded-sm border border-failed/30 bg-failed-soft p-3">
          <p className="flex items-start gap-1.5 text-sm font-medium"><AlertTriangle className="mt-0.5 size-4 shrink-0 text-failed" aria-hidden="true" />Request chưa có ngày hiệu lực hợp lệ – nhập ngày để quyền được đổi đúng ngày.</p>
          <Field label="Ngày hiệu lực" className="mt-2 max-w-xs"><Input type="date" required value={eff} onChange={(e) => setEff(e.target.value)} /></Field>
        </div>
      ) : <p className="text-sm font-medium">Áp dụng ngày {formatDate(o.effective_date)}</p>}
      <div className="overflow-x-auto">
        <table className="w-full text-left text-sm">
          <caption className="sr-only">Trước và sau thuyên chuyển</caption>
          <thead className="text-muted"><tr><th scope="col" className="py-1.5">Thông tin</th><th scope="col" className="py-1.5">Trước <From>AD hiện tại</From></th><th scope="col" className="py-1.5">Sau</th></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label} className="border-t border-line align-top">
                <th scope="row" className="py-1.5 pr-3 font-normal text-muted">{r.label}</th>
                <td className="break-all py-1.5 pr-3">{r.before || "—"}</td>
                <td className="break-all py-1.5">{r.after ? <>{r.after}<From>{r.from}</From></> : <span className="text-muted">Giữ nguyên</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {mayEdit && ous.length > 0 && (
        <Field label="OU đích" className="max-w-xl">
          <Select value={ou} onChange={(e) => setOu(e.target.value)}>
            {!planOu && <option value="">Giữ nguyên OU hiện tại</option>}
            {ous.map((x) => <option key={x} value={x}>{x}</option>)}
          </Select>
        </Field>
      )}
      <section aria-labelledby="tp-add" className="space-y-1">
        <h3 id="tp-add" className="text-base font-semibold">Nhóm thêm ({(o.add ?? []).length})</h3>
        {(o.add ?? []).length === 0 ? <p className="text-sm text-muted">Không có nhóm cần thêm.</p> : (
          <ul className="space-y-1 text-sm">{(o.add ?? []).map((g) => <li key={g} className="flex items-center gap-1.5 break-all"><Plus className="size-4 shrink-0 text-done" aria-hidden="true" />{g}</li>)}</ul>
        )}
      </section>
      <fieldset className="space-y-1.5">
        <legend className="mb-1 text-base font-semibold">Nhóm gỡ ({remove.length})</legend>
        {remove.length === 0 && <p className="text-sm text-muted">Không có nhóm cần gỡ.</p>}
        {remove.map((g) => (
          <label key={g} className="flex items-start gap-2 text-sm">
            <input
              type="checkbox"
              className="mt-0.5 size-4 accent-[var(--primary)]"
              checked={kept.includes(g)}
              disabled={!mayEdit}
              onChange={(e) => setKept((k) => (e.target.checked ? [...k, g] : k.filter((x) => x !== g)))}
            />
            <span className="break-all">{g}</span>
          </label>
        ))}
      </fieldset>
      {addLines("tp-lic-add", `License thêm${lic.level ? ` (cấp ${lic.level}${lic.level_why ? ` – ${lic.level_why}` : ""})` : ""}`, lic.add ?? [], skuName)}
      <RemoveLines id="tp-lic-rm" title="License gỡ" items={lic.remove ?? []} label={skuName} kept={keptLic} setKept={setKeptLic} locked={!mayEdit} />
      {addLines("tp-bio-add", "Nhóm quyền BioStar thêm", bio.add ?? [], (x) => `Nhóm ${x}`)}
      <RemoveLines id="tp-bio-rm" title="Nhóm quyền BioStar gỡ" items={bio.remove ?? []} label={(x) => `Nhóm ${x}`} kept={keptBio} setKept={setKeptBio} locked={!mayEdit} />
      {((remove.length + (lic.remove ?? []).length + (bio.remove ?? []).length) > 0 && !mayEdit) && <p className="flex items-center gap-1 text-xs text-muted"><Minus className="size-3" aria-hidden="true" />Chỉ người có quyền “Chuyển Job” được bỏ chọn dòng cần gỡ.</p>}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" variant="primary" loading={busy} disabled={!!o.blocked || (needsDate && !eff)}>Chốt kế hoạch thuyên chuyển</Button>
        {onReprepare && (o.blocked || needsDate) && <ReprepareButton onReprepare={onReprepare} busy={reprepareBusy} />}
      </div>
    </form>
  );
}

interface TransferNeed { label: string; quantity: number; catalog_name?: string }
interface TransferExcess { id?: string; name: string; asset_tag?: string | null; tag?: string | null; product_type?: string | null }

/** Transfer `transfer_assets`: devices to add for the new level and ones above the standard; acknowledging is required when there are any. */
export function TransferAssetsPanel({ job, step, onSubmit, busy }: { job: Job; step: JobStep; onSubmit: (data: Record<string, unknown>) => void; busy: boolean }) {
  const { can } = useAuth();
  const o = step.output as { level?: string | null; needs?: TransferNeed[]; excess?: TransferExcess[] };
  const needs = o.needs ?? [];
  const excess = o.excess ?? [];
  const emp = job.params.employee;
  const link = (n: TransferNeed) => {
    // buyLink words the reason for onboarding; the proposal form only knows source "onboard", so only the reason is replaced.
    const u = new URL(buyLink({ id: job.params.request_id, display_id: job.params.request_display_id, full_name: emp.full_name, employee_id: emp.id, band: o.level ?? null, effective_date: job.params.effective_date }, { label: n.label, catalog_name: n.catalog_name ?? "", buy: n.quantity }, `/lifecycle/jobs/${job.id}`), "http://x");
    u.searchParams.set("reason", `Thuyên chuyển ${emp.full_name}, cấp ${o.level ?? "?"}: chưa đủ ${n.label} theo tiêu chuẩn.`);
    return `${u.pathname}${u.search}`;
  };
  return (
    <div className="space-y-4">
      <section aria-labelledby="ta-needs" className="space-y-1">
        <h3 id="ta-needs" className="text-base font-semibold">Cần bổ sung ({needs.length})</h3>
        {needs.length === 0 ? <p className="text-sm text-muted">Đủ thiết bị theo tiêu chuẩn cấp mới.</p> : (
          <ul className="divide-y divide-line rounded-sm border border-line">
            {needs.map((n) => (
              <li key={n.label} className="flex flex-wrap items-center gap-2 px-3 py-1.5 text-sm">
                <span className="min-w-0 flex-1">{n.label} × {n.quantity}</span>
                {can("workload.purchase.create") && (
                  <Link to={link(n)} className="inline-flex h-8 items-center gap-1.5 rounded-sm border border-line bg-surface px-3 font-medium text-ink hover:bg-surface-2">
                    <ShoppingCart className="size-4" aria-hidden="true" />Lập đề xuất mua
                  </Link>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>
      <section aria-labelledby="ta-excess" className="space-y-1">
        <h3 id="ta-excess" className="text-base font-semibold">Vượt chuẩn ({excess.length})</h3>
        {excess.length === 0 ? <p className="text-sm text-muted">Không có thiết bị vượt chuẩn.</p> : (
          <ul className="divide-y divide-line rounded-sm border border-line">
            {excess.map((a, i) => (
              <li key={a.id ?? i} className="px-3 py-1.5 text-sm">{a.name} <span className="text-muted">{a.product_type} · <Mono>{a.asset_tag ?? a.tag ?? "—"}</Mono></span></li>
            ))}
          </ul>
        )}
      </section>
      <Button variant="primary" loading={busy} onClick={() => onSubmit({ acknowledged: true })}>
        {needs.length || excess.length ? "Xác nhận đã bổ sung / xem xét thu hồi" : "Hoàn tất"}
      </Button>
    </div>
  );
}
