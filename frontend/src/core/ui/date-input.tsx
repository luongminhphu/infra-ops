import { forwardRef, useEffect, useRef, useState, type InputHTMLAttributes, type KeyboardEvent } from "react";
import { CalendarDays, ChevronLeft, ChevronRight } from "lucide-react";
import { cn } from "@/core/lib/cn";
import { Input } from "./field";

/** "dd/MM/yyyy" → "yyyy-MM-dd", or null when incomplete/invalid. */
export function parseVnDate(text: string): string | null {
  const m = /^(\d{1,2})\/(\d{1,2})\/(\d{4})$/.exec(text.trim());
  if (!m) return null;
  const [d, mo, y] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const date = new Date(Date.UTC(y, mo - 1, d));
  if (date.getUTCFullYear() !== y || date.getUTCMonth() !== mo - 1 || date.getUTCDate() !== d) return null;
  return `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
}

/** Accept "01112026", "1/11/2026", "01/11/2026"; insert slashes while typing forward. */
export function formatTyped(raw: string, forward: boolean): string {
  const cleaned = raw.replace(/[^\d/]/g, "");
  if (cleaned.includes("/")) {
    if (forward && (/^\d{2}$/.test(cleaned) || /^\d{1,2}\/\d{2}$/.test(cleaned))) return `${cleaned}/`;
    return cleaned.slice(0, 10);
  }
  const digits = cleaned.slice(0, 8);
  const parts = [digits.slice(0, 2), digits.slice(2, 4), digits.slice(4, 8)].filter(Boolean);
  let out = parts.join("/");
  if (forward && (digits.length === 2 || digits.length === 4)) out += "/";
  return out;
}

export function toVnDate(iso: string): string {
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : "";
}

const pad = (n: number) => String(n).padStart(2, "0");
const iso = (y: number, m: number, d: number) => {
  const dt = new Date(Date.UTC(y, m, d));
  return `${dt.getUTCFullYear()}-${pad(dt.getUTCMonth() + 1)}-${pad(dt.getUTCDate())}`;
};
const parts = (s: string) => s.split("-").map(Number) as [number, number, number];
/** Shift an ISO date by days (or months, clamped to the month's last day). */
export function shiftDate(value: string, days: number, months = 0): string {
  const [y, m, d] = parts(value);
  if (!months) return iso(y, m - 1, d + days);
  const last = new Date(Date.UTC(y, m - 1 + months + 1, 0)).getUTCDate();
  return iso(y, m - 1 + months, Math.min(d, last));
}
/** The 6×7 grid (Monday first) of ISO dates around a month. */
export function monthGrid(year: number, month: number): string[] {
  const first = new Date(Date.UTC(year, month, 1)).getUTCDay(); // 0 = Sunday
  const start = 1 - ((first + 6) % 7);
  return Array.from({ length: 42 }, (_, i) => iso(year, month, start + i));
}
const todayIso = () => {
  const now = new Date(new Date().toLocaleString("en-US", { timeZone: "Asia/Ho_Chi_Minh" }));
  return iso(now.getFullYear(), now.getMonth(), now.getDate());
};
const WEEKDAYS = ["T2", "T3", "T4", "T5", "T6", "T7", "CN"];

/** Month calendar: arrows move by day / week, PageUp/PageDown by month, Enter picks, Escape closes. */
function Calendar({ value, onPick, onClose }: { value: string; onPick: (iso: string) => void; onClose: () => void }) {
  const [focus, setFocus] = useState(value || todayIso());
  const [y, m] = parts(focus);
  const grid = monthGrid(y, m - 1);
  const gridRef = useRef<HTMLDivElement>(null);
  const today = todayIso();
  useEffect(() => {
    gridRef.current?.querySelector<HTMLButtonElement>(`[data-day="${focus}"]`)?.focus();
  }, [focus]);
  const key = (e: KeyboardEvent) => {
    const move: Record<string, () => string> = {
      ArrowLeft: () => shiftDate(focus, -1),
      ArrowRight: () => shiftDate(focus, 1),
      ArrowUp: () => shiftDate(focus, -7),
      ArrowDown: () => shiftDate(focus, 7),
      PageUp: () => shiftDate(focus, 0, -1),
      PageDown: () => shiftDate(focus, 0, 1),
    };
    if (move[e.key]) {
      e.preventDefault();
      setFocus(move[e.key]!());
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  };
  return (
    <div role="dialog" aria-label="Chọn ngày" className="absolute top-full left-0 z-30 mt-1 w-72 rounded-md border border-line-strong bg-surface p-2 shadow-card" onKeyDown={key}>
      <div className="mb-1 flex items-center justify-between">
        <button type="button" className="grid size-8 place-items-center rounded-sm text-muted hover:bg-surface-2 hover:text-ink" aria-label="Tháng trước" onClick={() => setFocus(shiftDate(focus, 0, -1))}>
          <ChevronLeft className="size-4" aria-hidden="true" />
        </button>
        <span className="text-sm font-semibold tabular" aria-live="polite">Tháng {m}/{y}</span>
        <button type="button" className="grid size-8 place-items-center rounded-sm text-muted hover:bg-surface-2 hover:text-ink" aria-label="Tháng sau" onClick={() => setFocus(shiftDate(focus, 0, 1))}>
          <ChevronRight className="size-4" aria-hidden="true" />
        </button>
      </div>
      <div ref={gridRef} role="grid" aria-label={`Tháng ${m} năm ${y}`} className="grid grid-cols-7 gap-0.5 text-center text-sm">
        {WEEKDAYS.map((w) => <span key={w} role="columnheader" className="py-1 text-xs font-medium text-muted">{w}</span>)}
        {grid.map((d) => {
          const [, dm, dd] = parts(d);
          const out = dm !== m;
          const picked = d === value;
          return (
            <button
              key={d}
              type="button"
              role="gridcell"
              data-day={d}
              tabIndex={d === focus ? 0 : -1}
              aria-selected={picked}
              aria-label={`${pad(dd)}/${pad(dm)}/${parts(d)[0]}${d === today ? " (hôm nay)" : ""}`}
              onClick={() => onPick(d)}
              className={cn(
                "grid h-8 place-items-center rounded-sm tabular hover:bg-surface-2",
                out && "text-muted",
                d === today && !picked && "font-semibold text-primary ring-1 ring-primary/40",
                picked && "bg-primary text-on-primary hover:bg-primary-hover",
              )}
            >
              {dd}
            </button>
          );
        })}
      </div>
      <div className="mt-1 flex justify-between border-t border-line pt-1">
        <button type="button" className="rounded-sm px-2 py-1 text-sm font-medium text-primary hover:bg-surface-2" onClick={() => onPick(today)}>Hôm nay</button>
        <button type="button" className="rounded-sm px-2 py-1 text-sm text-muted hover:bg-surface-2 hover:text-ink" onClick={onClose}>Đóng</button>
      </div>
    </div>
  );
}

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> & {
  /** ISO yyyy-MM-dd or "" */
  value: string;
  onChange: (iso: string) => void;
  onInvalidChange?: (invalid: boolean) => void;
};

/**
 * Date entry in the app's dd/MM/yyyy format, independent of the browser locale
 * (native date inputs show mm/dd/yyyy on English systems). Type it, or pick it on the calendar button. Emits ISO
 * dates.
 */
export const DateInput = forwardRef<HTMLInputElement, Props>(function DateInput(
  { value, onChange, onInvalidChange, className, ...props },
  ref,
) {
  const [text, setText] = useState(toVnDate(value));
  const [open, setOpen] = useState(false);
  const wrap = useRef<HTMLSpanElement>(null);
  const toggle = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    if ((parseVnDate(text) ?? "") !== value) setText(toVnDate(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  useEffect(() => {
    if (!open) return;
    const away = (e: MouseEvent) => !wrap.current?.contains(e.target as Node) && setOpen(false);
    document.addEventListener("mousedown", away);
    return () => document.removeEventListener("mousedown", away);
  }, [open]);
  const cls = (className ?? "w-40 tabular").split(/\s+/).filter(Boolean);
  // Width classes size the wrapper (the calendar button sits inside the field); the rest style the input.
  const widths = cls.filter((c) => /^(min-|max-)?w-/.test(c));
  const rest = cls.filter((c) => !widths.includes(c));
  const close = () => { setOpen(false); toggle.current?.focus(); };
  return (
    <span ref={wrap} className={cn("relative block", widths.length ? widths : "w-40")}>
    <Input
      ref={ref}
      inputMode="numeric"
      placeholder="dd/mm/yyyy"
      autoComplete="off"
      maxLength={10}
      value={text}
      className={cn(rest, "w-full pr-9")}
      onChange={(e) => {
        // Auto-insert slashes while typing digits.
        const t = formatTyped(e.target.value, e.target.value.length > text.length);
        setText(t);
        const iso = parseVnDate(t);
        if (iso) onChange(iso);
        else if (t === "") onChange("");
        onInvalidChange?.(t !== "" && !iso);
      }}
      {...props}
    />
      <button
        ref={toggle}
        type="button"
        disabled={props.disabled}
        aria-label="Chọn ngày trên lịch"
        aria-expanded={open}
        aria-haspopup="dialog"
        onClick={() => setOpen(!open)}
        className="absolute top-1/2 right-1 grid size-7 -translate-y-1/2 place-items-center rounded-sm text-muted hover:bg-surface-2 hover:text-ink disabled:opacity-50"
      >
        <CalendarDays className="size-4" aria-hidden="true" />
      </button>
      {open && (
        <Calendar
          value={value}
          onClose={close}
          onPick={(d) => {
            setText(toVnDate(d));
            onChange(d);
            onInvalidChange?.(false);
            close();
          }}
        />
      )}
    </span>
  );
});
