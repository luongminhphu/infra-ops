import { forwardRef, useEffect, useState, type InputHTMLAttributes } from "react";
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

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type"> & {
  /** ISO yyyy-MM-dd or "" */
  value: string;
  onChange: (iso: string) => void;
  onInvalidChange?: (invalid: boolean) => void;
};

/**
 * Date entry in the app's dd/MM/yyyy format, independent of the browser locale
 * (native date inputs show mm/dd/yyyy on English systems). Emits ISO dates.
 */
export const DateInput = forwardRef<HTMLInputElement, Props>(function DateInput(
  { value, onChange, onInvalidChange, className, ...props },
  ref,
) {
  const [text, setText] = useState(toVnDate(value));
  useEffect(() => {
    if ((parseVnDate(text) ?? "") !== value) setText(toVnDate(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <Input
      ref={ref}
      inputMode="numeric"
      placeholder="dd/mm/yyyy"
      autoComplete="off"
      maxLength={10}
      value={text}
      className={className ?? "w-36 tabular"}
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
  );
});
