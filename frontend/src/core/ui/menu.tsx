import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { cn } from "@/core/lib/cn";

/**
 * Minimal accessible menu button: Esc and outside click close, arrow keys move between
 * items, focus returns to the trigger on close.
 */
export function Menu({
  trigger,
  label,
  children,
  align = "end",
}: {
  trigger: (props: { "aria-expanded": boolean; "aria-controls": string; "aria-haspopup": "menu"; onClick: () => void }) => ReactNode;
  label: string;
  children: ReactNode;
  align?: "start" | "end";
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const menu = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const items = () => Array.from(menu.current?.querySelectorAll<HTMLElement>("[role^=menuitem]") ?? []);
    items()[0]?.focus();
    const onDoc = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        (root.current?.querySelector("[aria-haspopup]") as HTMLElement | null)?.focus();
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        const list = items();
        const i = list.indexOf(document.activeElement as HTMLElement);
        const next = e.key === "ArrowDown" ? (i + 1) % list.length : (i - 1 + list.length) % list.length;
        list[next]?.focus();
      }
    };
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  return (
    <div ref={root} className="relative">
      {trigger({ "aria-expanded": open, "aria-controls": id, "aria-haspopup": "menu", onClick: () => setOpen((v) => !v) })}
      {open && (
        <div
          ref={menu}
          id={id}
          role="menu"
          aria-label={label}
          onClick={(e) => {
            if ((e.target as HTMLElement).closest("[role=menuitem],[role=menuitemradio]")) setOpen(false);
          }}
          className={cn(
            "absolute top-full z-50 mt-1 min-w-56 rounded-md border border-line bg-surface p-1 shadow-lg",
            align === "end" ? "right-0" : "left-0",
          )}
        >
          {children}
        </div>
      )}
    </div>
  );
}

export function MenuItem({
  children,
  onSelect,
  checked,
  danger,
}: {
  children: ReactNode;
  onSelect: () => void;
  checked?: boolean;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      role={checked === undefined ? "menuitem" : "menuitemradio"}
      aria-checked={checked}
      tabIndex={-1}
      onClick={onSelect}
      className={cn(
        "flex w-full cursor-pointer items-center gap-2 rounded-xs px-2 py-1.5 text-left text-base hover:bg-surface-2 focus-visible:bg-surface-2",
        danger && "text-failed",
      )}
    >
      {children}
    </button>
  );
}

export function MenuLabel({ children }: { children: ReactNode }) {
  return <div className="px-2 pt-1.5 pb-1 text-xs text-muted">{children}</div>;
}

export function MenuSeparator() {
  return <div role="separator" className="my-1 h-px bg-line" />;
}
