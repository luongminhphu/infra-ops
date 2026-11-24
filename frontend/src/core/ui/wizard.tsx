import { useEffect, useRef, type ReactNode } from "react";
import { Check, ChevronLeft } from "lucide-react";
import { cn } from "@/core/lib/cn";
import { Button } from "./button";

export interface WizardStep {
  key: string;
  title: string;
}

/**
 * One thing at a time: a progress strip, the current step's question and content, and one primary
 * action ("Đúng – Tiếp tục"). Focus moves to the step heading when the step changes so keyboard and
 * screen-reader users land on the new question (or to the step's `[data-autofocus]` field when it has one). On phones the action bar sits above the tab bar.
 */
export function Wizard({
  label,
  steps,
  current,
  title,
  description,
  children,
  onBack,
  next,
  aside,
}: {
  label: string;
  steps: WizardStep[];
  current: number;
  title: string;
  description?: ReactNode;
  children: ReactNode;
  onBack?: () => void;
  next?: { label: ReactNode; onClick: () => void; disabled?: boolean; loading?: boolean; reason?: string | null };
  /** Secondary actions next to the primary one (e.g. "Chọn tài sản khác"). */
  aside?: ReactNode;
}) {
  const heading = useRef<HTMLHeadingElement>(null);
  const first = useRef(true);
  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    // A step can name its first field (data-autofocus, e.g. a scanner input); otherwise read the question.
    const field = heading.current?.closest("section")?.querySelector<HTMLElement>("[data-autofocus]");
    (field ?? heading.current)?.focus();
  }, [current]);
  const reasonId = `wizard-why-${label.replace(/\W+/g, "-")}`;

  return (
    <section aria-label={label} className="mx-auto w-full max-w-3xl">
      <WizardProgress steps={steps} current={current} />

      <div className="rounded-md border border-line bg-surface shadow-card">
        <div className="border-b border-line px-5 py-4">
          <h2 ref={heading} tabIndex={-1} className="text-lg font-semibold outline-none">
            {title}
          </h2>
          {description && <p className="mt-0.5 text-sm text-pretty text-muted">{description}</p>}
        </div>
        <div className="px-5 py-4">{children}</div>
        {(next || onBack || aside) && (
          <div className="sticky bottom-[calc(var(--tabbar-h)+env(safe-area-inset-bottom))] z-20 flex flex-wrap items-center gap-2 rounded-b-md border-t border-line bg-surface px-5 py-3 sm:bottom-0">
            {onBack && (
              <Button variant="ghost" onClick={onBack}>
                <ChevronLeft className="size-4" aria-hidden="true" />Quay lại
              </Button>
            )}
            {next?.reason && (
              <span id={reasonId} className="text-sm text-waiting">
                {next.reason}
              </span>
            )}
            <span className="ml-auto flex flex-wrap gap-2">
              {aside}
              {next && (
                <Button
                  variant="primary"
                  disabled={next.disabled}
                  loading={next.loading}
                  aria-describedby={next.reason ? reasonId : undefined}
                  onClick={next.onClick}
                >
                  {next.label}
                </Button>
              )}
            </span>
          </div>
        )}
      </div>
    </section>
  );
}

/** Progress of a wizard: numbered strip for short flows on wide screens, "Bước N/M" + bar otherwise. */
export function WizardProgress({ steps, current, className }: { steps: WizardStep[]; current: number; className?: string }) {
  const strip = steps.length <= 5;
  return (
    <div className={cn("mb-4", className)}>
      {strip && (
        <ol aria-label="Các bước" className="hidden items-center gap-2 sm:flex">
          {steps.map((s, i) => {
            const done = i < current;
            const now = i === current;
            return (
              <li key={s.key} className="flex min-w-0 flex-1 items-center gap-2" aria-current={now ? "step" : undefined}>
                <span
                  className={cn(
                    "flex size-7 shrink-0 items-center justify-center rounded-full text-sm font-semibold tabular",
                    done ? "bg-done-soft text-done" : now ? "bg-primary text-on-primary" : "bg-surface-2 text-muted",
                  )}
                >
                  {done ? <Check className="size-4" aria-label="Đã xong" /> : i + 1}
                </span>
                <span className={cn("truncate text-sm", now ? "font-semibold text-ink" : "text-muted")}>{s.title}</span>
                {i < steps.length - 1 && <span aria-hidden="true" className={cn("h-px flex-1", done ? "bg-done" : "bg-line")} />}
              </li>
            );
          })}
        </ol>
      )}
      <div className={cn(strip && "sm:hidden")}>
        <p className="text-sm text-muted">
          <span className="tabular">Bước {current + 1}/{steps.length}</span>
          {!strip && current + 1 < steps.length && <> · tiếp theo: {steps[current + 1]!.title}</>}
        </p>
        <div className="mt-1 h-1.5 rounded-full bg-surface-2" aria-hidden="true">
          <div className="h-1.5 rounded-full bg-primary" style={{ width: `${((current + 1) / steps.length) * 100}%` }} />
        </div>
      </div>
    </div>
  );
}
