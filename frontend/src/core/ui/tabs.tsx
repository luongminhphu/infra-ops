import { useId, useRef, type KeyboardEvent, type ReactNode } from "react";
import { cn } from "@/core/lib/cn";

export interface TabDef {
  key: string;
  label: ReactNode;
}

/** WAI-ARIA tabs with roving tabindex and arrow-key navigation. */
export function Tabs({
  tabs,
  value,
  onChange,
  children,
  label,
}: {
  tabs: TabDef[];
  value: string;
  onChange: (key: string) => void;
  children: ReactNode;
  label: string;
}) {
  const id = useId();
  const refs = useRef<Record<string, HTMLButtonElement | null>>({});
  const onKey = (e: KeyboardEvent) => {
    const i = tabs.findIndex((t) => t.key === value);
    let next = -1;
    if (e.key === "ArrowRight") next = (i + 1) % tabs.length;
    if (e.key === "ArrowLeft") next = (i - 1 + tabs.length) % tabs.length;
    if (e.key === "Home") next = 0;
    if (e.key === "End") next = tabs.length - 1;
    if (next >= 0) {
      e.preventDefault();
      const key = tabs[next]!.key;
      onChange(key);
      refs.current[key]?.focus();
    }
  };
  return (
    <div>
      <div role="tablist" aria-label={label} className="flex gap-1 border-b border-line" onKeyDown={onKey}>
        {tabs.map((t) => {
          const selected = t.key === value;
          return (
            <button
              key={t.key}
              ref={(el) => {
                refs.current[t.key] = el;
              }}
              id={`${id}-tab-${t.key}`}
              role="tab"
              type="button"
              aria-selected={selected}
              aria-controls={`${id}-panel`}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(t.key)}
              className={cn(
                "-mb-px cursor-pointer border-b-2 px-3 py-2 text-base font-medium transition-colors duration-150",
                selected ? "border-primary text-ink" : "border-transparent text-muted hover:text-ink",
              )}
            >
              {t.label}
            </button>
          );
        })}
      </div>
      <div role="tabpanel" id={`${id}-panel`} aria-labelledby={`${id}-tab-${value}`} className="pt-4">
        {children}
      </div>
    </div>
  );
}
