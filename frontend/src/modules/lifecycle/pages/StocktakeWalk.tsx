import { useMemo, useRef, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CircleCheck, ChevronDown, PackagePlus, ScanLine, TriangleAlert, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { cn } from "@/core/lib/cn";
import { Button } from "@/core/ui/button";
import { Mono, Panel } from "@/core/ui/feedback";
import { Field, Input, Select } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import type { Item, ZoneRow } from "./StocktakePage";

type Condition = "good" | "damaged" | "broken";
const CONDITION: Record<Condition, string> = { good: "Tốt", damaged: "Hư hỏng nhẹ", broken: "Hỏng" };

/** What the walker sees for a device: its type and the code on the sticker. */
export const deviceTitle = (i: Item) => `${i.product_type || "Thiết bị"} · ${i.asset_tag || i.serial || i.name}`;

export interface Desk {
  key: string;
  person: string;
  employeeId: string | null;
  items: Item[];
  left: number;
}

/** One floor's assets as desks (one per person, then the assets nobody uses), desks still to check first. */
export function desks(items: Item[]): Desk[] {
  const by = new Map<string, Desk>();
  for (const i of items) {
    const key = i.employee_id || i.user_name || (i.count === "extra" ? "~extra" : "~none");
    const person = i.user_name || (i.count === "extra" ? "Máy ngoài sổ SDP" : "Không có người dùng (kho, dùng chung)");
    const d = by.get(key) ?? { key, person, employeeId: i.employee_id, items: [], left: 0 };
    d.items.push(i);
    if (i.count === "pending") d.left += 1;
    by.set(key, d);
  }
  return [...by.values()].sort((a, b) => Number(a.left === 0) - Number(b.left === 0) || a.person.localeCompare(b.person, "vi"));
}

/** Book assets whose codes contain what was scanned (a partial or mis-read code), at most 3. */
export function nearMatches(items: Item[], code: string): Item[] {
  const c = code.trim().toUpperCase();
  if (c.length < 3) return [];
  return items.filter((i) => [i.serial, i.asset_tag, i.fa_code].some((v) => v && v.toUpperCase().includes(c))).slice(0, 3);
}

interface Last {
  tone: "done" | "skipped" | "warning" | "failed";
  title: string;
  detail?: string;
  item?: Item;
  code?: string;
  near?: Item[];
}

/**
 * Đi kiểm: the Helpdesk walking a floor desk by desk with a phone or a barcode scanner.
 * One floor at a time, the scan bar stays on screen, each desk confirms in one tap; the place is the floor.
 */
export function StocktakeWalk({
  stocktakeId,
  items,
  zones,
  mine,
  onSaved,
  onExtra,
}: {
  stocktakeId: string;
  items: Item[];
  zones: ZoneRow[];
  mine: string[];
  onSaved: () => void;
  onExtra: (code: string, seenAt: string) => void;
}) {
  const floors = zones.filter((z) => z.id);
  const [zoneId, setZoneId] = useState<string>(mine[0] ?? floors[0]?.id ?? "");
  const zone = floors.find((z) => z.id === zoneId);
  const here = useMemo(() => items.filter((i) => i.zone_id === zoneId), [items, zoneId]);
  const floorDesks = useMemo(() => desks(here), [here]);
  const left = here.filter((i) => i.count === "pending").length;
  const book = here.filter((i) => i.count !== "extra").length;
  const damaged = here.filter((i) => i.condition && i.condition !== "good").length;
  const extras = here.filter((i) => i.count === "extra").length;
  const zoneName = new Map(floors.map((z) => [z.id ?? "", z.name]));
  const seenAt = zone?.name ?? "";

  const [code, setCode] = useState("");
  const [last, setLast] = useState<Last | null>(null);
  const scanRef = useRef<HTMLInputElement>(null);

  const put = (i: Item, body: { count: "found" | "pending"; condition?: Condition | null; note?: string }) =>
    api<Item>(`/api/lifecycle/stocktakes/${stocktakeId}/items/${i.id}`, {
      method: "PUT",
      json: { condition: null, note: i.note, ...body, seen_at: body.count === "found" ? seenAt : "" },
    });

  const tick = useMutation({
    mutationFn: async (list: Item[]) => {
      for (const i of list) await put(i, { count: "found", condition: "good" });
      return list;
    },
    onSuccess: (list) => {
      onSaved();
      toast.success(list.length > 1 ? `Đã ghi nhận ${list.length} thiết bị: Có, tình trạng tốt.` : `${deviceTitle(list[0]!)} – Có.`, {
        action: { label: "Hoàn tác", onClick: () => void Promise.all(list.map((i) => put(i, { count: "pending" }))).then(onSaved) },
      });
    },
    onError: (e) => toast.error(errorMessage(e)),
  });

  const scan = useMutation({
    mutationFn: (c: string) =>
      api<{ matched: boolean; already: boolean; item: Item | null }>(`/api/lifecycle/stocktakes/${stocktakeId}/scan`, {
        method: "POST",
        json: { code: c, seen_at: seenAt },
      }),
    onSuccess: (r, c) => {
      const i = r.item;
      if (!r.matched) {
        const near = nearMatches(items, c);
        setLast(
          near.length
            ? { tone: "warning", title: `Không có mã “${c}”. Có phải một trong các máy này?`, near }
            : { tone: "warning", title: `“${c}” không có trong sổ SDP của kỳ này.`, detail: "Kiểm tra lại mã trên tem; nếu đúng là máy chưa có trên SDP thì thêm vào danh sách ngoài sổ.", code: c },
        );
      } else if (i) {
        const elsewhere = i.zone_id && i.zone_id !== zoneId ? ` · sổ ghi ${zoneName.get(i.zone_id) ?? "khu khác"}` : "";
        setLast({
          tone: r.already ? "skipped" : elsewhere ? "warning" : "done",
          title: r.already ? `${deviceTitle(i)} – đã kiểm trước đó` : `${deviceTitle(i)} – Có`,
          detail: `${i.user_name ?? "Không có người dùng"}${elsewhere}${elsewhere ? " – vẫn ghi nhận Có, ghi chú nơi thấy là khu này." : ""}`,
          item: i,
        });
      }
      setCode("");
      onSaved();
      scanRef.current?.focus();
    },
    onError: (e) => setLast({ tone: "failed", title: errorMessage(e) }),
  });

  return (
    <div className="grid gap-4">
      <div role="group" aria-label="Khu đang kiểm" className="flex flex-wrap items-center gap-2">
        {floors
          .filter((z) => mine.includes(z.id!) || z.id === zoneId)
          .map((z) => {
            const zl = items.filter((i) => i.zone_id === z.id && i.count === "pending").length;
            return (
              <button
                key={z.id}
                type="button"
                aria-pressed={z.id === zoneId}
                onClick={() => { setZoneId(z.id!); setLast(null); }}
                className={cn(
                  "inline-flex min-h-11 items-center gap-2 rounded-md border px-3 text-base transition-colors duration-150",
                  z.id === zoneId ? "border-primary bg-primary-soft font-medium text-primary" : "border-line-strong bg-surface hover:bg-surface-2",
                )}
              >
                {z.name}
                <span className="tabular text-sm text-muted">{zl ? `còn ${zl}` : "xong"}</span>
              </button>
            );
          })}
        <Select aria-label="Kiểm khu khác" value="" onChange={(e) => e.target.value && setZoneId(e.target.value)} className="min-h-11 w-auto">
          <option value="">Khu khác…</option>
          {floors.filter((z) => !mine.includes(z.id!) && z.id !== zoneId).map((z) => <option key={z.id} value={z.id!}>{z.name}</option>)}
        </Select>
      </div>

      {/* The scan bar stays under the top bar while the walker scrolls through the desks. */}
      <div className="sticky top-[var(--topbar-h)] z-20 -mx-4 border-b border-line bg-surface/95 px-4 py-3 backdrop-blur sm:mx-0 sm:rounded-md sm:border">
        <form
          className="flex items-end gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            if (code.trim()) scan.mutate(code.trim());
          }}
        >
          <Field label={<span className="inline-flex items-center gap-1.5">Quét tem hoặc gõ mã<HelpTip title="Quét mã"><span className="block">Serial, asset tag hoặc Mã TSCĐ. Máy quét mã vạch gõ mã rồi Enter; ô luôn sẵn sàng cho máy tiếp theo.</span><span className="block">Nơi thấy tự ghi là khu đang chọn ({seenAt}).</span></HelpTip></span>} className="min-w-0 flex-1">
            <Input ref={scanRef} value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" autoFocus inputMode="text" className="h-11 font-mono text-lg" aria-describedby="scan-result" />
          </Field>
          <Button type="submit" variant="primary" loading={scan.isPending} className="h-11 shrink-0">
            {!scan.isPending && <ScanLine className="size-5" aria-hidden="true" />}Ghi nhận
          </Button>
        </form>
        <div id="scan-result" role="status" aria-live="polite" className="empty:hidden">
          {last && (
            <div className={cn("mt-3 rounded-md border px-3 py-2", last.tone === "done" ? "border-done bg-done-soft" : last.tone === "failed" ? "border-failed bg-failed-soft" : last.tone === "skipped" ? "border-line bg-surface-2" : "border-waiting bg-waiting-soft")}>
              <p className="flex flex-wrap items-center gap-2 font-medium">
                <StatusBadge status={last.tone === "done" ? "done" : last.tone === "failed" ? "failed" : last.tone === "skipped" ? "skipped" : "warning"} label={last.tone === "done" ? "Có" : last.tone === "skipped" ? "Đã kiểm" : last.tone === "failed" ? "Lỗi" : "Cần xem"} />
                {last.title}
              </p>
              {last.detail && <p className="mt-0.5 text-sm text-pretty">{last.detail}</p>}
              {last.near && (
                <ul className="mt-2 grid gap-1">
                  {last.near.map((i) => (
                    <li key={i.id} className="flex flex-wrap items-center gap-2 text-sm">
                      <span className="min-w-0 flex-1">{deviceTitle(i)} <span className="text-muted">· {i.user_name ?? "kho"} · <Mono>{i.serial}</Mono></span></span>
                      {i.count === "pending" ? <Button size="sm" className="min-h-9" onClick={() => { tick.mutate([i]); setLast(null); }}>Đúng máy này: Có</Button> : <StatusBadge status="done" label="Đã kiểm" />}
                    </li>
                  ))}
                </ul>
              )}
              {last.code && <Button size="sm" variant="link" onClick={() => onExtra(last.code!, seenAt)}>Thêm máy ngoài sổ</Button>}
            </div>
          )}
        </div>
      </div>

      {zone && (
        <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-sm">
          <span className="text-base font-semibold">{zone.name}</span>
          <span className="tabular">{book - left} / {book} thiết bị đã kiểm</span>
          {damaged > 0 && <span className="tabular">{damaged} hỏng</span>}
          {extras > 0 && <span className="tabular">{extras} ngoài sổ</span>}
        </p>
      )}
      {zone && left === 0 && book > 0 && (
        <div role="status" className="flex items-center gap-3 rounded-md border border-done bg-done-soft px-4 py-3">
          <CircleCheck className="size-5 shrink-0 text-done" aria-hidden="true" />
          <p><b className="font-semibold">{zone.name} xong.</b> {book}/{book} thiết bị{damaged ? ` · ${damaged} hỏng` : ""}{extras ? ` · ${extras} ngoài sổ` : ""}. {mine.some((id) => id !== zoneId && items.some((i) => i.zone_id === id && i.count === "pending")) ? "Chọn khu tiếp theo ở trên." : "Bạn đã kiểm xong các khu được giao."}</p>
        </div>
      )}

      <ul className="grid gap-3" aria-label={`Các bàn ở ${zone?.name ?? "khu này"}`}>
        {floorDesks.map((d) => <DeskCard key={d.key} desk={d} busy={tick.isPending} onTick={(list) => tick.mutate(list)} put={put} onSaved={onSaved} />)}
      </ul>
      {here.length === 0 && <p className="text-sm text-muted">Khu này không có tài sản trên sổ.</p>}

      <div>
        <Button onClick={() => onExtra("", seenAt)} className="min-h-11"><PackagePlus className="size-4" aria-hidden="true" />Thấy máy không có trong sổ</Button>
      </div>
    </div>
  );
}

function DeskCard({ desk: d, busy, onTick, put, onSaved }: { desk: Desk; busy: boolean; onTick: (items: Item[]) => void; put: (i: Item, body: { count: "found" | "pending"; condition?: Condition | null; note?: string }) => Promise<Item>; onSaved: () => void }) {
  const pending = d.items.filter((i) => i.count === "pending");
  const done = pending.length === 0;
  return (
    <li>
      <Panel
        title={
          <span className="flex flex-wrap items-center gap-2">
            {d.person}
            {d.employeeId && <Mono className="text-sm font-normal text-muted">{d.employeeId}</Mono>}
            {done && <StatusBadge status="done" label="Bàn này xong" />}
          </span>
        }
        actions={pending.length > 1 && <Button variant="primary" className="min-h-11" loading={busy} onClick={() => onTick(pending)}>Có đủ {pending.length} máy</Button>}
        bodyClassName="p-0"
        className={done ? "opacity-80" : undefined}
      >
        <ul className="divide-y divide-line">
          {d.items.map((i) => <DeviceRow key={i.id} item={i} busy={busy} onTick={() => onTick([i])} put={put} onSaved={onSaved} />)}
        </ul>
      </Panel>
    </li>
  );
}

function DeviceRow({ item: i, busy, onTick, put, onSaved }: { item: Item; busy: boolean; onTick: () => void; put: (i: Item, body: { count: "found" | "pending"; condition?: Condition | null; note?: string }) => Promise<Item>; onSaved: () => void }) {
  const [open, setOpen] = useState(false);
  const [condition, setCondition] = useState<Condition>((i.condition as Condition | null) ?? "damaged");
  const [note, setNote] = useState(i.note);
  const save = useMutation({
    mutationFn: (body: { count: "found" | "pending"; condition?: Condition | null; note?: string }) => put(i, body),
    onSuccess: () => { setOpen(false); onSaved(); },
    onError: (e) => toast.error(errorMessage(e)),
  });
  const counted = i.count === "found" || i.count === "extra";
  return (
    <li className="px-4 py-3">
      <div className="flex items-center gap-3">
        <div className="min-w-0 flex-1">
          <p className="font-medium">{deviceTitle(i)}</p>
          <p className="text-sm text-muted">Serial <Mono>{i.serial || "—"}</Mono>{i.fa_code && <> · TSCĐ <Mono>{i.fa_code}</Mono></>}</p>
          {counted && i.condition && i.condition !== "good" && <p className="mt-1 text-sm"><StatusBadge status="warning" label={CONDITION[i.condition as Condition]} />{i.note && <span className="ml-2">{i.note}</span>}</p>}
        </div>
        {i.count === "pending" ? (
          <div className="flex shrink-0 items-center gap-1">
            <Button variant="ghost" className="min-h-11 px-2" aria-expanded={open} aria-label={`Có nhưng hỏng – ${deviceTitle(i)}`} onClick={() => setOpen(!open)}>
              <TriangleAlert className="size-4" aria-hidden="true" />Hỏng<ChevronDown className="size-4" aria-hidden="true" />
            </Button>
            <Button className="min-h-11 min-w-16" loading={busy} aria-label={`Có – ${deviceTitle(i)}`} onClick={onTick}>Có</Button>
          </div>
        ) : i.count === "found" ? (
          <div className="flex shrink-0 items-center gap-2">
            <StatusBadge status="done" label="Có" />
            <Button size="sm" variant="ghost" className="min-h-9" loading={save.isPending} aria-label={`Hoàn tác – ${deviceTitle(i)}`} onClick={() => save.mutate({ count: "pending" })}><Undo2 className="size-4" aria-hidden="true" /></Button>
          </div>
        ) : (
          <StatusBadge status="mismatch" label="Ngoài sổ" />
        )}
      </div>
      {open && (
        <form
          className="mt-3 grid gap-3 rounded-md bg-surface-2 p-3 sm:grid-cols-[auto_1fr_auto] sm:items-end"
          onSubmit={(e) => { e.preventDefault(); save.mutate({ count: "found", condition, note: note.trim() }); }}
        >
          <Field label="Tình trạng">
            <Select value={condition} onChange={(e) => setCondition(e.target.value as Condition)} className="min-h-11">
              <option value="damaged">{CONDITION.damaged}</option>
              <option value="broken">{CONDITION.broken}</option>
            </Select>
          </Field>
          <Field label="Mô tả ngắn"><Input value={note} onChange={(e) => setNote(e.target.value)} maxLength={1000} placeholder="vd. nứt màn hình, mất phím" className="min-h-11" /></Field>
          <Button type="submit" variant="primary" className="min-h-11" loading={save.isPending}>Lưu: Có, hỏng</Button>
        </form>
      )}
    </li>
  );
}
