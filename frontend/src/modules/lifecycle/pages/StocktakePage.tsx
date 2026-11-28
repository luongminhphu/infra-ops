import { useRef, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronRight, ClipboardCheck, Download, FileSignature, PackagePlus, Upload } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { AssetTagLink, EmpLink } from "./EmployeePanel";
import { useAuth } from "@/core/auth/AuthProvider";
import { formatDate, formatDateTime } from "@/core/lib/format";
import { Button, buttonVariants } from "@/core/ui/button";
import { DateInput } from "@/core/ui/date-input";
import { Dialog } from "@/core/ui/dialog";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge, type Status } from "@/core/ui/status";
import { Tabs } from "@/core/ui/tabs";
import { AreasPanel } from "./StocktakeAreas";
import { StocktakeFinance } from "./StocktakeFinance";
import { CONDITION, type Condition } from "./stocktakeShared";
import { StocktakeWalk } from "./StocktakeWalk";

export type Count = "pending" | "found" | "missing" | "extra";

export interface Item {
  id: number;
  asset_id: string | null;
  name: string;
  product_type: string | null;
  asset_tag: string | null;
  serial: string | null;
  fa_code: string | null;
  state: string | null;
  user_name: string | null;
  employee_id: string | null;
  department: string | null;
  site: string | null;
  zone_id: string | null;
  count: Count;
  condition: Condition | null;
  seen_at: string;
  note: string;
  counted_by: string | null;
  resolution: string | null;
  resolution_note: string;
  /** Set by "Áp hướng xử lý gợi ý": still waits for a person to confirm. */
  resolution_suggested?: boolean;
  difference: string | null;
}

interface Summary {
  book: number;
  pending: number;
  found: number;
  missing: number;
  extra: number;
  differences: number;
  unresolved: number;
  /** Only with view_all: assets in no zone yet. */
  unzoned?: number;
}

export interface ZoneRow extends Summary {
  id: string | null;
  name: string;
  is_store: boolean;
  assignee_user_id: string | null;
  assignee_name: string | null;
}

export interface RecordInfo {
  id: string;
  status: "draft" | "finalized" | "signed" | "void";
  number: string | null;
  has_pdf: boolean;
  has_signed: boolean;
  data: { it_rep: string; it_title: string; finance_rep: string; finance_title: string; conclusion: string };
}

interface Head {
  record?: RecordInfo | null;
  zones: ZoneRow[];
  id: string;
  name: string;
  cutoff: string;
  status: "counting" | "reviewing" | "closed";
  status_label: string;
  rules_version?: number;
  source_synced_at: string | null;
  created_by: string;
  closed_at: string | null;
}

const COUNT: Record<Count, { label: string; status: Status }> = {
  pending: { label: "Chưa kiểm", status: "pending" },
  found: { label: "Đã thấy", status: "done" },
  missing: { label: "Không tìm thấy", status: "failed" },
  extra: { label: "Ngoài sổ SDP", status: "mismatch" },
};
const STATUS: Record<Head["status"], Status> = { counting: "in_progress", reviewing: "waiting", closed: "done" };
const SHOWN = 200;
type Mode = "walk" | "manage" | "finance";
/** The ways into a stocktake: walking a floor, the whole picture to follow up and finish, and the accounting reconciliation. */
const MODES: Record<Mode, [string, string]> = {
  walk: ["Đi kiểm", "Quét tem hoặc bấm Đã thấy từng máy ở khu bạn được giao, theo từng bàn."],
  manage: ["Tổng hợp & xử lý", "Tiến độ các khu, giao khu, danh sách đầy đủ, chênh lệch và hướng xử lý, kết thúc kiểm, xuất file."],
  finance: ["Đối chiếu Kế toán", "Nhập sổ TSCĐ / CCDC của Kế toán và đối chiếu với sổ SDP và kết quả kiểm."],
};

/** What kind of difference an item is (the key of the allowed resolutions); null when books and site agree. */
export function diffKind(i: Item, rulesVersion = 2): string | null {
  if (i.count === "missing" || i.count === "extra") return i.count;
  if (i.count === "found" && i.condition === "broken") return "broken";
  if (i.count === "found" && i.condition === "damaged" && rulesVersion < 2) return "damaged";
  return null;
}

/** Resolutions a row's kind of difference accepts: [key, label]. A kind the server lists nothing for accepts all. */
export function allowedResolutions(kind: string | null, meta: { resolutions: Record<string, string>; allowed?: Record<string, string[]> }): [string, string][] {
  const keys = (kind && meta.allowed?.[kind]) || Object.keys(meta.resolutions);
  return keys.filter((k) => meta.resolutions[k]).map((k) => [k, meta.resolutions[k]!]);
}

export type View = "all" | "pending" | "missing" | "found" | "differences" | "extra";

/** Items of one tab, narrowed by a search over name, codes, user and site. */
/** "mine" = the zones handed to me, "none" = assets in no zone, "" = every zone, else one zone id. */
export type ZoneFilter = string;

/** Items of one tab and zone, narrowed by a search over name, codes, user and site; sorted by person. */
export function viewItems(items: Item[], view: View, query: string, zone: ZoneFilter = "", mine: string[] = []): Item[] {
  const q = query.trim().toLowerCase();
  const inZone = (i: Item) =>
    !zone || (zone === "mine" ? mine.includes(i.zone_id ?? "") : zone === "none" ? !i.zone_id : i.zone_id === zone);
  return items.filter(
    (i) =>
      inZone(i) &&
      (view === "all" ||
        (view === "differences" ? Boolean(i.difference) : view === "found" ? i.count === "found" : i.count === view)) &&
      (!q || [i.name, i.asset_tag, i.serial, i.fa_code, i.user_name, i.employee_id, i.department, i.site].some((v) => v?.toLowerCase().includes(q))),
  ).sort((a, b) => person(a).localeCompare(person(b), "vi") || a.id - b.id);
}

/** Who the asset is with: counting goes desk by desk. */
export const person = (i: Item) => (i.user_name ? `${i.user_name}${i.employee_id ? ` · ${i.employee_id}` : ""}` : i.count === "extra" ? "Ngoài sổ SDP" : "Không có người dùng");

// ---------------------------------------------------------------- list

export function StocktakesPage() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const q = useQuery({
    queryKey: ["lifecycle", "stocktakes"],
    queryFn: () => api<{ items: (Head & { book: number })[]; last_sync: string | null }>("/api/lifecycle/stocktakes"),
  });
  return (
    <>
      <PageHeader
        title="Kiểm kê tài sản IT"
        description="Kiểm kê cuối năm: chốt sổ tài sản từ SDP, kiểm thực tế từng máy, xử lý chênh lệch, rồi gửi file cho Hành chánh và Kế toán đối chiếu."
        actions={can("lifecycle.stocktake.manage") && <Button variant="primary" onClick={() => setOpen(true)}><ClipboardCheck className="size-4" aria-hidden="true" />Mở kỳ kiểm kê</Button>}
      />
      {q.isLoading && <LoadingRows />}
      {q.isError && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.data && q.data.items.length === 0 && (
        <EmptyState icon={ClipboardCheck} title="Chưa có kỳ kiểm kê">
          Supervisor/Manager mở kỳ kiểm kê; app chốt sổ theo lần đồng bộ SDP gần nhất ({formatDateTime(q.data.last_sync, "chưa đồng bộ")}).
        </EmptyState>
      )}
      {q.data && q.data.items.length > 0 && (
        <ul className="divide-y divide-line border-y border-line">
          {q.data.items.map((s) => (
            <li key={s.id}>
              <Link to={`/lifecycle/stocktake/${s.id}`} className="flex min-h-14 flex-wrap items-center gap-x-4 gap-y-1 px-1 py-2 hover:text-primary">
                <span className="min-w-0 basis-full font-medium sm:basis-auto sm:flex-1">{s.name}</span>
                <span className="text-sm text-muted tabular">Chốt sổ {formatDate(s.cutoff)} · {s.book} tài sản</span>
                <StatusBadge status={STATUS[s.status]} label={s.status_label} />
                <ChevronRight className="size-4 text-muted" aria-hidden="true" />
              </Link>
            </li>
          ))}
        </ul>
      )}
      {can("lifecycle.stocktake.view_all") && <AreasPanel />}
      <CreateDialog open={open} onClose={() => setOpen(false)} lastSync={q.data?.last_sync ?? null} onCreated={(id) => navigate(`/lifecycle/stocktake/${id}`)} />
    </>
  );
}

function CreateDialog({ open, onClose, lastSync, onCreated }: { open: boolean; onClose: () => void; lastSync: string | null; onCreated: (id: string) => void }) {
  const year = new Date().getFullYear();
  const [name, setName] = useState(`Kiểm kê tài sản IT ${year}`);
  const [cutoff, setCutoff] = useState(`${year}-12-31`);
  const [bad, setBad] = useState(false);
  const qc = useQueryClient();
  const create = useMutation({
    mutationFn: () => api<Head>("/api/lifecycle/stocktakes", { method: "POST", json: { name, cutoff } }),
    onSuccess: async (s) => {
      await qc.invalidateQueries({ queryKey: ["lifecycle", "stocktakes"] });
      onClose();
      onCreated(s.id);
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Mở kỳ kiểm kê"
      description={<>Sổ sách là danh sách tài sản IT của lần đồng bộ SDP gần nhất ({formatDateTime(lastSync, "chưa đồng bộ")}). Đồng bộ lại trước nếu SDP vừa thay đổi.</>}
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" loading={create.isPending} disabled={!name.trim() || !cutoff || bad} onClick={() => create.mutate()}>Chốt sổ và mở kỳ</Button>
        </>
      }
    >
      <div className="grid gap-4">
        <Field label="Tên kỳ" required>
          <Input value={name} onChange={(e) => setName(e.target.value)} maxLength={200} />
        </Field>
        <Field label="Ngày chốt sổ" required error="Nhập ngày dạng dd/mm/yyyy." showError={bad}>
          <DateInput value={cutoff} onChange={setCutoff} onInvalidChange={setBad} />
        </Field>
      </div>
    </Dialog>
  );
}

// ---------------------------------------------------------------- one stocktake

export function StocktakePage() {
  const { id = "" } = useParams();
  const { can, me } = useAuth();
  const qc = useQueryClient();
  const key = ["lifecycle", "stocktake", id];
  const q = useQuery({ queryKey: key, queryFn: () => api<Head & { summary: Summary; items: Item[] }>(`/api/lifecycle/stocktakes/${id}`) });
  const [view, setView] = useState<View>("differences");
  const [touchedView, setTouchedView] = useState(false);
  const [query, setQuery] = useState("");
  const [extra, setExtra] = useState<{ code: string; zoneId?: string } | null>(null);
  const [zone, setZone] = useState<ZoneFilter | null>(null);
  const [params, setParams] = useSearchParams();
  const [confirmFinish, setConfirmFinish] = useState(false);
  const [confirmClose, setConfirmClose] = useState(false);
  const [confirmDefaults, setConfirmDefaults] = useState(false);
  const refresh = () => qc.invalidateQueries({ queryKey: key });

  const step = useMutation({
    mutationFn: (path: "finish-count" | "close") => api(`/api/lifecycle/stocktakes/${id}/${path}`, { method: "POST" }),
    onSuccess: async (_r, path) => {
      toast.success(path === "close" ? "Đã đóng kỳ kiểm kê." : "Đã kết thúc kiểm – xử lý các chênh lệch.");
      setView("differences");
      setConfirmFinish(false);
      setConfirmClose(false);
      await refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const defaults = useMutation({
    mutationFn: () => api<{ suggested: number }>(`/api/lifecycle/stocktakes/${id}/default-resolutions`, { method: "POST" }),
    onSuccess: async (r) => {
      toast.success(`Đã gợi ý hướng xử lý cho ${r.suggested} chênh lệch – xác nhận từng dòng để hoàn tất.`);
      setConfirmDefaults(false);
      await refresh();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  if (q.isLoading) return <LoadingRows />;
  if (q.isError || !q.data) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const s = q.data;
  const open = s.status !== "closed";
  const counting = can("lifecycle.stocktake.count") && open;
  const manage = can("lifecycle.stocktake.manage");
  const finance = can("lifecycle.stocktake.finance");
  // Administrator / Manager hand zones to Operators (lifecycle.stocktake.assign).
  const assign = can("lifecycle.stocktake.assign");
  // Counting starts on what is left to count; afterwards on the differences.
  const current: View = view === "differences" && s.status === "counting" && !touchedView ? "pending" : view;
  const mine = s.zones.filter((z) => z.assignee_user_id && z.assignee_user_id === me?.id).map((z) => z.id ?? "");
  // Opens on my zones when I have some.
  const zoneNow = zone ?? (mine.length ? "mine" : "");
  const shown = viewItems(s.items, current, query, zoneNow, mine);
  const zoneName = new Map(s.zones.map((z) => [z.id ?? "", z.name]));
  // Tab counts follow the zone and search the list is showing.
  const n = (v: View) => viewItems(s.items, v, query, zoneNow, mine).length;
  const tabs: { key: View; label: string; n: number }[] = [
    s.status === "counting" ? { key: "pending", label: "Chưa kiểm", n: n("pending") } : { key: "missing", label: "Không tìm thấy", n: n("missing") },
    { key: "found", label: "Đã thấy", n: n("found") },
    { key: "differences", label: "Chênh lệch", n: n("differences") },
    { key: "extra", label: "Ngoài sổ SDP", n: n("extra") },
    { key: "all", label: "Tất cả", n: n("all") },
  ];
  // Đi kiểm (walking a floor) for whoever counts while the count is on; Tổng hợp & xử lý for the rest of the job.
  const canWalk = counting && s.status === "counting" && s.zones.some((z) => z.id);
  // The whole picture (every zone, differences, Excel files) is for lifecycle.stocktake.view_all; an Operator only walks.
  const overview = can("lifecycle.stocktake.view_all");
  const avail: Mode[] = [...(canWalk ? ["walk" as const] : []), ...(overview || !canWalk ? ["manage" as const] : []), ...(finance ? ["finance" as const] : [])];
  const want = params.get("mode") ?? (canWalk && (mine.length || !manage) ? "walk" : "manage");
  const mode: Mode = avail.find((m) => m === want) ?? avail[0]!;
  const setMode = (m: string) => setParams((p) => { p.set("mode", m); return p; }, { replace: true });
  const withoutResolution = s.items.filter((i) => i.difference && !i.resolution).length;
  const unfinished = s.zones.filter((z) => z.pending > 0);

  const content = (
    <>
      {mode === "walk" ? (
        <StocktakeWalk stocktakeId={id} items={s.items} zones={s.zones} mine={mine} canSeeAll={overview} onSaved={refresh} onExtra={(code, zoneId) => setExtra({ code, zoneId })} />
      ) : mode === "finance" ? (
        <StocktakeFinance stocktakeId={id} open={open} canFix={manage} items={s.items} onItemSaved={refresh} />
      ) : !overview ? (
        <div role="status" className="flex flex-wrap items-center gap-3 rounded-md border border-line bg-surface-2 px-4 py-3">
          <StatusBadge status={STATUS[s.status]} label={s.status_label} />
          <p className="text-sm text-pretty">Phần kiểm thực tế đã kết thúc. Tổng hợp, xử lý chênh lệch và biên bản do Supervisor/Manager thực hiện.</p>
        </div>
      ) : (
      <>
      <div className="mb-4 flex flex-wrap items-center gap-x-6 gap-y-2 text-sm">
        <StatusBadge status={STATUS[s.status]} label={s.status_label} />
        <span className="tabular"><b className="font-semibold">{s.summary.found}</b> / {s.summary.book} tài sản trên sổ đã thấy</span>
        {s.summary.extra > 0 && <span className="tabular">{s.summary.extra} ngoài sổ SDP</span>}
        {s.status !== "counting" && <span className="tabular">{s.summary.differences} chênh lệch · {s.summary.unresolved} chưa có hướng xử lý</span>}
        <HelpTip title="Các bước kiểm kê">
          <span className="block">1. Đang kiểm: Manager giao khu; Helpdesk mở “Đi kiểm”, quét tem hoặc bấm Đã thấy từng máy theo bàn.</span>
          <span className="block">2. Kết thúc kiểm (Supervisor/Manager): máy chưa thấy thành “Không tìm thấy”. Vẫn sửa được nếu tìm ra sau.</span>
          <span className="block">3. Chọn hướng xử lý cho mọi chênh lệch (đã thống nhất với Hành chánh / Kế toán), rồi Đóng kỳ. File Excel có sheet Sổ SDP để HC/KT đối chiếu với sổ của mình.</span>
        </HelpTip>
        {manage && s.status === "reviewing" && withoutResolution > 0 && (
          <Button size="sm" loading={defaults.isPending} onClick={() => setConfirmDefaults(true)}>Áp hướng xử lý gợi ý</Button>
        )}
      </div>
      {(s.summary.unzoned ?? 0) > 0 && open && (
        <div role="status" className="mb-4 flex flex-wrap items-center gap-3 rounded-md border border-waiting bg-waiting-soft px-4 py-3">
          <StatusBadge status="warning" label="Chưa phân khu" />
          <p className="min-w-0 flex-1 text-sm text-pretty">{s.summary.unzoned} tài sản chưa thuộc khu nào nên chưa ai kiểm được. Chuyển chúng vào khu ở bảng “Chia việc theo khu”.</p>
          <Button size="sm" onClick={() => { setTouchedView(true); setView("all"); setZone("none"); }}>Xem các tài sản này</Button>
          <a href="#zones-panel" className="text-sm text-primary hover:underline">Đến bảng chia khu</a>
        </div>
      )}
      {s.status === "closed" && <RecordPanel stocktakeId={id} record={s.record ?? null} canMake={manage} onSaved={refresh} />}
      {s.zones.length > 0 && <div id="zones-panel"><ZonesPanel stocktakeId={id} zones={s.zones} canAssign={assign && open} onSaved={refresh} /></div>}
      <Panel
        title="Tài sản"
        actions={counting && <Button size="sm" onClick={() => setExtra({ code: "" })}><PackagePlus className="size-4" aria-hidden="true" />Thêm tài sản ngoài sổ</Button>}
        bodyClassName="p-0"
      >
        <div className="px-4 pt-3">
          <Tabs label="Lọc tài sản" value={current} onChange={(k) => { setTouchedView(true); setView(k as View); }} tabs={tabs.map((t) => ({ key: t.key, label: <>{t.label} <span className="tabular text-muted">{t.n}</span></> }))}>
            <div className="flex flex-wrap gap-3 py-3">
              {s.zones.length > 0 && (
                <Select aria-label="Khu" value={zoneNow} onChange={(e) => setZone(e.target.value)} className="w-auto">
                  <option value="">Mọi khu</option>
                  {mine.length > 0 && <option value="mine">Khu của tôi</option>}
                  {s.zones.map((z) => <option key={z.id ?? "none"} value={z.id ?? "none"}>{z.name}{z.assignee_name ? ` – ${z.assignee_name}` : ""}</option>)}
                </Select>
              )}
              <Input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Tìm tên, serial, asset tag, Mã TSCĐ, người dùng, Mã NV, phòng ban" aria-label="Tìm tài sản" className="max-w-md flex-1" />
            </div>
          </Tabs>
        </div>
        {shown.length === 0 ? (
          <p className="px-4 pb-4 text-sm text-muted">Không có tài sản nào ở mục này.</p>
        ) : (
          <ul className="divide-y divide-line border-t border-line">
            {shown.slice(0, SHOWN).map((i, n, all) => (
              <ItemRow
                key={i.id}
                item={i}
                heading={n === 0 || person(all[n - 1]!) !== person(i) ? person(i) : null}
                zoneName={i.zone_id ? zoneName.get(i.zone_id) ?? "" : "Chưa phân khu"}
                zones={assign && open && s.zones.length > 0 ? s.zones : null}
                stocktakeId={id}
                status={s.status}
                canCount={counting}
                canResolve={manage && open}
                rulesVersion={s.rules_version ?? 2}
                onSaved={refresh}
              />
            ))}
          </ul>
        )}
        {shown.length > SHOWN && <p className="px-4 py-3 text-sm text-muted">Đang hiện {SHOWN} / {shown.length} – tìm để thu hẹp.</p>}
      </Panel>
      </>
      )}
    </>
  );

  return (
    <>
      <PageHeader
        title={s.name}
        description={<>Chốt sổ {formatDate(s.cutoff)} · dữ liệu SDP lúc {formatDateTime(s.source_synced_at)} · mở bởi {s.created_by}</>}
        actions={
          <>
            {mode === "manage" && overview && <a href={`/api/lifecycle/stocktakes/${id}/export`} className={buttonVariants({ variant: "secondary" })}><Download className="size-4" aria-hidden="true" />File tổng (Excel)</a>}
            {manage && s.status === "counting" && mode === "manage" && <Button variant="primary" loading={step.isPending} onClick={() => setConfirmFinish(true)}>Kết thúc kiểm</Button>}
            {manage && s.status === "reviewing" && (
              <Button variant="primary" loading={step.isPending} disabled={s.summary.unresolved > 0} onClick={() => setConfirmClose(true)}>
                Đóng kỳ{s.summary.unresolved > 0 ? ` (còn ${s.summary.unresolved} chênh lệch)` : ""}
              </Button>
            )}
          </>
        }
      />
      {avail.length > 1 ? (
        <Tabs label="Chế độ" value={mode} onChange={setMode} tabs={avail.map((m) => ({ key: m, label: MODES[m][0] }))}>
          <p className="mb-4 text-sm text-muted text-pretty">{mode === "manage" && !manage ? "Xem tiến độ các khu và danh sách đầy đủ tài sản của kỳ kiểm kê." : MODES[mode][1]}</p>
          {content}
        </Tabs>
      ) : content}
      {extra && <ExtraDialog stocktakeId={id} code={extra.code} zoneId={extra.zoneId} onClose={() => setExtra(null)} onSaved={refresh} />}
      <Dialog
        open={confirmClose}
        onClose={() => setConfirmClose(false)}
        title="Đóng kỳ kiểm kê?"
        description="Không hoàn tác được: sau khi đóng kỳ, không sửa kết quả kiểm, hướng xử lý hay sổ Kế toán của kỳ này nữa. Biên bản kiểm kê được lập từ kỳ đã đóng."
        footer={
          <>
            <Button onClick={() => setConfirmClose(false)}>Chưa đóng</Button>
            <Button variant="danger" loading={step.isPending} onClick={() => step.mutate("close")}>Đóng kỳ kiểm kê</Button>
          </>
        }
      >
        <p className="text-sm">{s.summary.differences} chênh lệch đã có hướng xử lý. Hãy chắc là Hành chánh / Kế toán đã thống nhất.</p>
      </Dialog>
      <Dialog
        open={confirmDefaults}
        onClose={() => setConfirmDefaults(false)}
        title="Áp hướng xử lý gợi ý?"
        description="Chỉ điền cho các chênh lệch chưa có hướng xử lý: Không tìm thấy → Tìm thêm, Ngoài sổ SDP → Đề nghị KT ghi tăng, Hỏng → Đề nghị thanh lý / ghi giảm. Đây chỉ là gợi ý: mỗi dòng vẫn cần được xác nhận mới tính là đã xử lý."
        footer={
          <>
            <Button onClick={() => setConfirmDefaults(false)}>Huỷ</Button>
            <Button variant="primary" loading={defaults.isPending} onClick={() => defaults.mutate()}>Áp gợi ý cho {withoutResolution} chênh lệch</Button>
          </>
        }
      />
      <Dialog
        open={confirmFinish}
        onClose={() => setConfirmFinish(false)}
        title="Kết thúc kiểm?"
        description="Mọi tài sản chưa kiểm sẽ chuyển thành “Không tìm thấy” để xử lý chênh lệch. Vẫn sửa được nếu tìm ra sau."
        footer={
          <>
            <Button onClick={() => setConfirmFinish(false)}>Chưa, kiểm tiếp</Button>
            <Button variant="danger" loading={step.isPending} onClick={() => step.mutate("finish-count")}>Kết thúc: {s.summary.pending} thành Không tìm thấy</Button>
          </>
        }
      >
        {unfinished.length > 0 ? (
          <ul className="grid gap-1 text-sm">
            {unfinished.map((z) => <li key={z.id ?? "none"} className="flex justify-between gap-4"><span>{z.name}{z.assignee_name ? ` – ${z.assignee_name}` : " – chưa giao"}</span><span className="tabular">còn {z.pending}</span></li>)}
          </ul>
        ) : (
          <p className="text-sm">Mọi khu đã kiểm xong.</p>
        )}
      </Dialog>
    </>
  );
}

/** Who counts which zone, and how far each has got. */
function ZonesPanel({ stocktakeId, zones, canAssign, onSaved }: { stocktakeId: string; zones: ZoneRow[]; canAssign: boolean; onSaved: () => void }) {
  const counters = useQuery({
    queryKey: ["lifecycle", "stocktake-counters"],
    queryFn: () => api<{ id: string; name: string; upn: string }[]>("/api/lifecycle/stocktake-counters"),
    enabled: canAssign,
  });
  const assign = useMutation({
    mutationFn: ({ zoneId, userId }: { zoneId: string; userId: string }) =>
      api(`/api/lifecycle/stocktakes/${stocktakeId}/zones/${zoneId}`, { method: "PUT", json: { user_id: userId || null } }),
    onSuccess: onSaved,
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Panel title="Chia việc theo khu" className="mb-4" bodyClassName="p-0">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead className="text-left text-muted">
            <tr className="border-b border-line">
              <th scope="col" className="px-4 py-2 font-medium">Khu</th>
              <th scope="col" className="px-2 py-2 font-medium">Người kiểm</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">Đã thấy</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">Còn lại</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">Không thấy</th>
              <th scope="col" className="px-2 py-2 text-right font-medium">Chênh lệch</th>
              <th scope="col" className="px-4 py-2 text-right font-medium"><span className="sr-only">File khu</span></th>
            </tr>
          </thead>
          <tbody className="divide-y divide-line">
            {zones.map((z) => {
              const left = z.pending;
              return (
                <tr key={z.id ?? "none"}>
                  <th scope="row" className="px-4 py-2 text-left font-medium">{z.name}</th>
                  <td className="px-2 py-1.5">
                    {canAssign && z.id && counters.data ? (
                      <Select aria-label={`Người kiểm ${z.name}`} value={z.assignee_user_id ?? ""} onChange={(e) => assign.mutate({ zoneId: z.id!, userId: e.target.value })} className="h-7 w-auto max-sm:h-11">
                        <option value="">Chưa giao</option>
                        {counters.data.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                      </Select>
                    ) : (
                      z.assignee_name ?? <span className="text-muted">{z.id ? "Chưa giao" : "Manager chuyển vào khu"}</span>
                    )}
                  </td>
                  <td className="px-2 py-2 text-right tabular">{z.found} / {z.book}</td>
                  <td className="px-2 py-2 text-right tabular">{left > 0 ? left : <StatusBadge status="done" label="Xong" />}</td>
                  <td className="px-2 py-2 text-right tabular">{z.missing || "—"}</td>
                  <td className="px-2 py-2 text-right tabular">{z.differences || "—"}</td>
                  <td className="px-4 py-1.5 text-right">
                    <a href={`/api/lifecycle/stocktakes/${stocktakeId}/export?zone_id=${z.id ?? "none"}`} className="inline-flex items-center gap-1 text-primary hover:underline" aria-label={`Tải file kiểm kê ${z.name}`}>
                      <Download className="size-4" aria-hidden="true" />File khu
                    </a>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </Panel>
  );
}

const RECORD_STATUS: Record<RecordInfo["status"], { label: string; status: Status }> = {
  draft: { label: "Bản nháp", status: "pending" },
  finalized: { label: "Đã chốt – chờ ký", status: "waiting" },
  signed: { label: "Đã ký", status: "done" },
  void: { label: "Đã huỷ", status: "skipped" },
};

/** The signers of the latest other closed stocktake: only a suggestion for the new record. */
function usePreviousSigners(stocktakeId: string, enabled: boolean): RecordInfo["data"] | null {
  const list = useQuery({
    queryKey: ["lifecycle", "stocktakes"],
    queryFn: () => api<{ items: { id: string; status: string }[] }>("/api/lifecycle/stocktakes"),
    enabled,
  });
  const prevId = list.data?.items.find((x) => x.id !== stocktakeId && x.status === "closed")?.id;
  const prev = useQuery({
    queryKey: ["lifecycle", "stocktake", prevId],
    queryFn: () => api<{ record?: RecordInfo | null }>(`/api/lifecycle/stocktakes/${prevId}`),
    enabled: enabled && Boolean(prevId),
  });
  const d = prev.data?.record?.data;
  return d && (d.it_rep || d.finance_rep) ? d : null;
}

/** Biên bản kiểm kê signed by IT and Kế toán: drafted from the closed stocktake, numbered, printed, signed scan uploaded. */
function RecordPanel({ stocktakeId, record, canMake, onSaved }: { stocktakeId: string; record: RecordInfo | null; canMake: boolean; onSaved: () => void }) {
  const live = record && record.status !== "void" ? record : null;
  const [f, setF] = useState(live?.data ?? { it_rep: "", it_title: "", finance_rep: "", finance_title: "", conclusion: "" });
  const [touched, setTouched] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const ok = (msg: string) => () => { toast.success(msg); onSaved(); };
  const fail = (e: unknown) => toast.error(errorMessage(e));
  const make = useMutation({ mutationFn: () => api(`/api/lifecycle/stocktakes/${stocktakeId}/record`, { method: "POST", json: f }), onSuccess: ok(live ? "Đã cập nhật bản nháp biên bản." : "Đã lập bản nháp biên bản."), onError: fail });
  const finalize = useMutation({ mutationFn: () => api(`/api/core/documents/${live!.id}/finalize`, { method: "POST" }), onSuccess: ok("Đã chốt biên bản – in, ký rồi tải bản đã ký lên."), onError: fail });
  const upload = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.set("file", file); return api(`/api/core/documents/${live!.id}/signed`, { method: "POST", body: fd }); },
    onSuccess: ok("Đã lưu bản đã ký."),
    onError: fail,
  });
  const draft = !live || live.status === "draft";
  const previous = usePreviousSigners(stocktakeId, canMake && draft);
  const missing = !f.it_rep.trim() || !f.finance_rep.trim();
  const file = (which: string) => `/api/core/documents/${live!.id}/file/${which}`;
  return (
    <Panel
      title={<span className="inline-flex items-center gap-2">Biên bản kiểm kê{live && <StatusBadge {...RECORD_STATUS[live.status]} />}{live?.number && <Mono>{live.number}</Mono>}</span>}
      description="Hai bên ký xác nhận: IT và Kế toán. Số liệu, kết quả theo khu và chênh lệch lấy từ kỳ đã đóng."
      className="mb-4"
    >
      {canMake && draft && (
        <form
          className="grid gap-4 sm:grid-cols-2"
          onSubmit={(e) => { e.preventDefault(); setTouched(true); if (!missing) make.mutate(); }}
        >
          {previous && !f.it_rep.trim() && !f.finance_rep.trim() && (
            <div className="flex flex-wrap items-center gap-3 rounded-md bg-surface-2 px-3 py-2 text-sm sm:col-span-2">
              <span className="min-w-0 flex-1">Kỳ trước ký: IT {previous.it_rep || "—"} · Kế toán {previous.finance_rep || "—"}. Kiểm tra lại người ký kỳ này.</span>
              <Button size="sm" onClick={() => setF({ ...f, it_rep: previous.it_rep ?? "", it_title: previous.it_title ?? "", finance_rep: previous.finance_rep ?? "", finance_title: previous.finance_title ?? "" })}>Dùng lại người ký kỳ trước</Button>
            </div>
          )}
          <Field label="Đại diện IT" required error="Nhập họ tên đại diện IT." showError={touched && !f.it_rep.trim()}><Input value={f.it_rep} onChange={set("it_rep")} maxLength={200} /></Field>
          <Field label="Chức danh (IT)"><Input value={f.it_title} onChange={set("it_title")} maxLength={200} placeholder="vd. Trưởng nhóm IT" /></Field>
          <Field label="Đại diện Kế toán" required error="Nhập họ tên đại diện Kế toán." showError={touched && !f.finance_rep.trim()}><Input value={f.finance_rep} onChange={set("finance_rep")} maxLength={200} /></Field>
          <Field label="Chức danh (Kế toán)"><Input value={f.finance_title} onChange={set("finance_title")} maxLength={200} placeholder="vd. Kế toán TSCĐ" /></Field>
          <Field label="Kết luận" hint="Để trống: app tự ghi theo số chênh lệch." className="sm:col-span-2"><Input value={f.conclusion} onChange={set("conclusion")} maxLength={2000} /></Field>
          <div className="flex flex-wrap gap-2 sm:col-span-2">
            <Button type="submit" variant={live ? "secondary" : "primary"} loading={make.isPending}><FileSignature className="size-4" aria-hidden="true" />{live ? "Cập nhật bản nháp" : "Lập biên bản"}</Button>
            {live && <a href={`/api/core/documents/${live.id}/preview.pdf`} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "secondary" })}>Xem trước PDF</a>}
            {live && <Button variant="primary" loading={finalize.isPending} onClick={() => finalize.mutate()}>Chốt biên bản</Button>}
          </div>
        </form>
      )}
      {!draft && live && (
        <div className="flex flex-wrap items-center gap-2">
          {live.has_pdf && <a href={file("pdf")} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "secondary" })}><Download className="size-4" aria-hidden="true" />PDF để in, ký</a>}
          <a href={file("docx")} className={buttonVariants({ variant: "ghost" })}>DOCX</a>
          {live.has_signed && <a href={file("signed")} target="_blank" rel="noreferrer" className={buttonVariants({ variant: "secondary" })}>Bản đã ký</a>}
          {canMake && (
            <>
              <input ref={fileRef} type="file" accept=".pdf,.png,.jpg,.jpeg" className="sr-only" tabIndex={-1} aria-hidden="true" onChange={(e) => { const x = e.target.files?.[0]; e.target.value = ""; if (x) upload.mutate(x); }} />
              <Button variant={live.has_signed ? "ghost" : "primary"} loading={upload.isPending} onClick={() => fileRef.current?.click()}><Upload className="size-4" aria-hidden="true" />{live.has_signed ? "Tải lại bản đã ký" : "Tải bản đã ký lên"}</Button>
            </>
          )}
        </div>
      )}
      {!canMake && draft && <p className="text-sm text-muted">{live ? "Biên bản đang ở bản nháp." : "Chưa lập biên bản."} Supervisor/Manager lập và chốt biên bản.</p>}
    </Panel>
  );
}

function ItemRow({ item: i, heading, zoneName, zones, stocktakeId, status, canCount, canResolve, rulesVersion, onSaved }: { item: Item; heading: string | null; zoneName: string; zones: ZoneRow[] | null; stocktakeId: string; status: Head["status"]; canCount: boolean; canResolve: boolean; rulesVersion: number; onSaved: () => void }) {
  const move = useMutation({
    mutationFn: (zoneId: string) => api(`/api/lifecycle/stocktakes/${stocktakeId}/items/${i.id}/zone`, { method: "PUT", json: { zone_id: zoneId || null } }),
    onSuccess: onSaved,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const meta = useQuery({ queryKey: ["lifecycle", "stocktake-meta"], queryFn: () => api<{ resolutions: Record<string, string>; allowed?: Record<string, string[]> }>("/api/lifecycle/stocktake-meta"), staleTime: Infinity });
  const [note, setNote] = useState(i.resolution_note);
  const save = useMutation({
    mutationFn: (body: { count: Count; condition?: Condition | null }) =>
      api(`/api/lifecycle/stocktakes/${stocktakeId}/items/${i.id}`, { method: "PUT", json: { ...body, seen_at: i.seen_at, note: i.note } }),
    onSuccess: onSaved,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const resolve = useMutation({
    mutationFn: (resolution: string | null) => api(`/api/lifecycle/stocktakes/${stocktakeId}/items/${i.id}/resolution`, { method: "PUT", json: { resolution, note } }),
    onSuccess: onSaved,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const remove = useMutation({
    mutationFn: () => api(`/api/lifecycle/stocktakes/${stocktakeId}/extras/${i.id}`, { method: "DELETE" }),
    onSuccess: onSaved,
    onError: (e) => toast.error(errorMessage(e)),
  });
  const options = meta.data ? allowedResolutions(diffKind(i, rulesVersion), meta.data) : [];
  const book = i.count !== "extra";
  const [armed, setArmed] = useState(false);
  return (
    <li className="grid gap-2 px-4 py-3">
      {heading && <p className="-mx-4 -mt-3 mb-1 bg-surface-2 px-4 py-1.5 text-sm font-semibold">{heading}</p>}
      <div className="min-w-0">
        <p className="flex flex-wrap items-center gap-2">
          <span className="font-medium">{i.name}</span>
          <StatusBadge {...COUNT[i.count]} />
          {i.condition && i.condition !== "good" && <StatusBadge status="warning" label={CONDITION[i.condition]} />}
        </p>
        <p className="mt-0.5 flex flex-wrap gap-x-4 text-sm text-muted">
          {i.product_type && <span>{i.product_type}</span>}
          <span>Tag <Mono>{i.asset_tag ? <AssetTagLink tag={i.asset_tag} /> : "—"}</Mono></span>
          <span>Serial <Mono>{i.serial || "—"}</Mono></span>
          <span>Mã TSCĐ <Mono>{i.fa_code || "—"}</Mono></span>
          {i.user_name && <span>{i.user_name}{i.employee_id && <> · <EmpLink id={i.employee_id} /></>}</span>}
          {i.department && <span>{i.department}</span>}
          {i.site && <span>Site {i.site}</span>}
          <span>Khu {zoneName}</span>
          {i.seen_at && <span>Thấy ở {i.seen_at}</span>}
        </p>
        {i.difference && <p className="mt-1 text-sm">Chênh lệch: {i.difference}{i.resolution && meta.data && <> · <b className="font-semibold">{meta.data.resolutions[i.resolution]}</b>{i.resolution_suggested && <> <StatusBadge status="warning" label="Gợi ý" /></>}{i.resolution_note && ` – ${i.resolution_note}`}</>}</p>}
      </div>
      <div className="flex flex-wrap items-center gap-2 empty:hidden">
        {canCount && book && (
          <>
            {i.count !== "found" && <Button size="sm" className="max-sm:h-11" loading={save.isPending} onClick={() => save.mutate({ count: "found", condition: "good" })}>Đã thấy</Button>}
            {i.count === "found" && (
              <Select aria-label={`Tình trạng ${i.name}`} value={i.condition ?? "good"} onChange={(e) => save.mutate({ count: "found", condition: e.target.value as Condition })} className="h-7 w-auto max-sm:h-11">
                {Object.entries(CONDITION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            )}
            {i.count !== "missing" && status === "reviewing" && <Button size="sm" variant="ghost" className="max-sm:h-11" onClick={() => save.mutate({ count: "missing" })}>Không tìm thấy</Button>}
            {i.count === "found" && status === "counting" && <Button size="sm" variant="ghost" className="max-sm:h-11" onClick={() => save.mutate({ count: "pending" })}>Bỏ đánh dấu</Button>}
          </>
        )}
        {canCount && !book && (
          <Button size="sm" variant={armed ? "danger" : "ghost"} className="max-sm:h-11" loading={remove.isPending} onClick={() => (armed ? remove.mutate() : setArmed(true))} onBlur={() => setArmed(false)}>
            {armed ? "Bấm lần nữa để xoá" : "Xoá dòng"}
          </Button>
        )}
        {zones && (
          <Select aria-label={`Chuyển khu ${i.name}`} value={i.zone_id ?? ""} onChange={(e) => move.mutate(e.target.value)} className="h-7 w-auto max-sm:h-11">
            <option value="">Chưa phân khu</option>
            {zones.filter((z) => z.id).map((z) => <option key={z.id} value={z.id!}>{z.name}</option>)}
          </Select>
        )}
        {canResolve && i.difference && status !== "counting" && meta.data && (
          <>
            <Field label="Ghi chú xử lý" className="w-48">
              <Input value={note} onChange={(e) => setNote(e.target.value)} onBlur={() => i.resolution && note !== i.resolution_note && resolve.mutate(i.resolution)} aria-label={`Ghi chú xử lý ${i.name}`} className="h-7 max-sm:h-11" maxLength={1000} />
            </Field>
            <Field label="Hướng xử lý">
              <Select aria-label={`Hướng xử lý ${i.name}`} value={options.some(([k]) => k === i.resolution) ? i.resolution! : ""} onChange={(e) => resolve.mutate(e.target.value || null)} className="h-7 w-auto max-sm:h-11">
                <option value="">Chọn hướng xử lý…</option>
                {options.map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </Field>
            {i.resolution_suggested && i.resolution && (
              <Button size="sm" variant="primary" className="max-sm:h-11" loading={resolve.isPending} onClick={() => resolve.mutate(i.resolution)}>Xác nhận</Button>
            )}
          </>
        )}
      </div>
    </li>
  );
}

function ExtraDialog({ stocktakeId, code, zoneId, onClose, onSaved }: { stocktakeId: string; code: string; zoneId?: string; onClose: () => void; onSaved: () => void }) {
  const [f, setF] = useState({ name: "", product_type: "", serial: code, asset_tag: "", fa_code: "", condition: "good" as Condition, seen_at: "", note: "" });
  const [touched, setTouched] = useState(false);
  const set = (k: keyof typeof f) => (e: { target: { value: string } }) => setF({ ...f, [k]: e.target.value });
  const nameError = f.name.trim() ? "" : "Nhập tên tài sản.";
  const codeError = f.serial.trim() || f.asset_tag.trim() || f.fa_code.trim() ? "" : "Nhập ít nhất serial, asset tag hoặc Mã TSCĐ.";
  const add = useMutation({
    mutationFn: () => api(`/api/lifecycle/stocktakes/${stocktakeId}/extras`, { method: "POST", json: { ...f, zone_id: zoneId ?? null } }),
    onSuccess: () => {
      toast.success("Đã thêm tài sản ngoài sổ.");
      onSaved();
      onClose();
    },
    onError: (e) => toast.error(errorMessage(e)),
  });
  return (
    <Dialog
      open
      onClose={onClose}
      title="Thêm tài sản ngoài sổ SDP"
      description="Thiết bị có trên thực tế nhưng không có trong sổ SDP của kỳ này. Máy được ghi vào khu đang kiểm. Sau khi kết thúc kiểm, chọn hướng xử lý (thường là Đề nghị KT ghi tăng)."
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="primary" loading={add.isPending} onClick={() => { setTouched(true); if (!nameError && !codeError) add.mutate(); }}>Thêm</Button>
        </>
      }
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="Tên tài sản" required error={nameError} showError={touched} className="sm:col-span-2"><Input value={f.name} onChange={set("name")} maxLength={256} /></Field>
        <Field label="Serial" error={codeError} showError={touched}><Input value={f.serial} onChange={set("serial")} className="font-mono" maxLength={128} /></Field>
        <Field label="Asset tag"><Input value={f.asset_tag} onChange={set("asset_tag")} className="font-mono" maxLength={128} /></Field>
        <Field label="Mã TSCĐ"><Input value={f.fa_code} onChange={set("fa_code")} className="font-mono" maxLength={128} /></Field>
        <Field label="Loại"><Input value={f.product_type} onChange={set("product_type")} placeholder="vd. Laptop" maxLength={128} /></Field>
        <Field label="Tình trạng">
          <Select value={f.condition} onChange={set("condition")}>
            {Object.entries(CONDITION).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
          </Select>
        </Field>
        {!zoneId && <Field label="Nơi thấy"><Input value={f.seen_at} onChange={set("seen_at")} maxLength={256} /></Field>}
        <Field label="Ghi chú" className="sm:col-span-2"><Input value={f.note} onChange={set("note")} maxLength={1000} /></Field>
      </div>
    </Dialog>
  );
}
