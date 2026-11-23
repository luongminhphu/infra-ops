import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { CircleHelp } from "lucide-react";
import { cn } from "@/core/lib/cn";

/**
 * Operating guide next to a heading, field or action: a "?" button that opens a short explanation.
 * Children must be phrasing content (use <span className="block"> for paragraphs) because the tip
 * sits inside <p>, <legend> and headings. Click/tap/Enter toggles it (works on touch and keyboard, unlike hover tooltips); Esc, a click
 * outside or moving focus away closes it.
 */
export function HelpTip({ title, children, className }: { title: string; children: ReactNode; className?: string }) {
  const id = useId();
  const btn = useRef<HTMLButtonElement>(null);
  const pop = useRef<HTMLSpanElement>(null);
  const [open, setOpen] = useState(false);
  const [pos, setPos] = useState<{ top: number; left: number; width: number } | null>(null);

  useLayoutEffect(() => {
    if (!open) return;
    const r = btn.current!.getBoundingClientRect();
    const width = Math.min(320, window.innerWidth - 32);
    const left = Math.max(16, Math.min(r.left, window.innerWidth - width - 16));
    const h = pop.current?.offsetHeight ?? 0;
    const below = r.bottom + 6;
    setPos({ top: below + h > window.innerHeight - 8 ? Math.max(8, r.top - h - 6) : below, left, width });
  }, [open, pos?.width]);

  useEffect(() => {
    if (!open) return;
    const close = (e: Event) => {
      if (e instanceof KeyboardEvent) {
        if (e.key !== "Escape") return;
        btn.current?.focus();
      } else if (btn.current?.contains(e.target as Node) || pop.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onScroll = () => setOpen(false);
    document.addEventListener("keydown", close);
    document.addEventListener("pointerdown", close);
    window.addEventListener("scroll", onScroll, true);
    return () => {
      document.removeEventListener("keydown", close);
      document.removeEventListener("pointerdown", close);
      window.removeEventListener("scroll", onScroll, true);
    };
  }, [open]);

  return (
    <>
      <button
        ref={btn}
        type="button"
        aria-label={`Hướng dẫn: ${title}`}
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((o) => !o)}
        onBlur={(e) => !pop.current?.contains(e.relatedTarget as Node) && setOpen(false)}
        className={cn(
          "inline-flex size-6 shrink-0 items-center justify-center rounded-full align-middle text-muted hover:bg-surface-2 hover:text-ink focus-visible:outline-2 focus-visible:outline-primary",
          open && "bg-surface-2 text-ink",
          className,
        )}
      >
        <CircleHelp className="size-4" aria-hidden="true" />
      </button>
      {open && (
        // Inline span (valid inside <p>/<legend>), fixed-positioned so no ancestor clips it.
        <span
          ref={pop}
          id={id}
          role="note"
          aria-label={title}
          style={{ top: pos?.top ?? -9999, left: pos?.left ?? 0, width: pos?.width ?? 320 }}
          className="fixed z-50 block rounded-md border border-line bg-surface p-3 text-left text-sm font-normal leading-relaxed text-ink shadow-xl"
        >
          <span className="mb-1 block font-semibold">{title}</span>
          <span className="block space-y-1.5 text-muted [&_b]:font-medium [&_b]:text-ink">{children}</span>
        </span>
      )}
    </>
  );
}
