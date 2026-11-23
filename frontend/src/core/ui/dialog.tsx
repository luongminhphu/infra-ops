import { useEffect, useId, useRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/core/lib/cn";
import { Button } from "./button";

interface DialogProps {
  open: boolean;
  onClose: () => void;
  title: string;
  description?: ReactNode;
  children?: ReactNode;
  footer?: ReactNode;
  size?: "sm" | "md" | "lg";
  /** Accessible name override for the close button. */
  closeLabel?: string;
}

/**
 * Modal built on native <dialog> + showModal(): the browser handles focus trapping,
 * Esc, inertness of the page and the top layer – no role/aria-modal/focus-trap code.
 */
export function Dialog({ open, onClose, title, description, children, footer, size = "md", closeLabel }: DialogProps) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-labelledby={titleId}
      className={cn(
        "m-auto w-[calc(100vw-2rem)] rounded-md border border-line bg-surface p-0 text-ink shadow-xl",
        size === "sm" && "max-w-md",
        size === "md" && "max-w-xl",
        size === "lg" && "max-w-3xl",
      )}
    >
      {open && (
        <div className="flex max-h-[85dvh] flex-col">
          <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-3.5">
            <div>
              <h2 id={titleId} className="text-lg font-semibold">
                {title}
              </h2>
              {description && <div className="mt-0.5 text-sm text-muted">{description}</div>}
            </div>
            <Button variant="ghost" size="icon" aria-label={closeLabel ?? "Đóng"} onClick={onClose}>
              <X className="size-4" aria-hidden="true" />
            </Button>
          </header>
          {/* Scrollable region must be keyboard-focusable (WCAG 2.1.1). */}
          <div className="overflow-y-auto px-5 py-4" tabIndex={0} aria-label={title}>{children}</div>
          {footer && <footer className="flex justify-end gap-2 border-t border-line px-5 py-3">{footer}</footer>}
        </div>
      )}
    </dialog>
  );
}
