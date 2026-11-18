import { cn } from "@/core/lib/cn";
import { STATUS, type Status } from "./status";

export interface StepItem {
  key: string;
  title: string;
  status: Status;
  statusLabel: string;
  meta?: string;
}

/**
 * Vertical stepper: ordered steps with icon + label status. Each step is a button that selects it,
 * so the list doubles as navigation for the detail panel (aria-current marks the selected step).
 */
export function Stepper({
  steps,
  selected,
  onSelect,
  label,
}: {
  steps: StepItem[];
  selected: string | null;
  onSelect: (key: string) => void;
  label: string;
}) {
  return (
    <ol aria-label={label} className="relative">
      {steps.map((s, i) => {
        const def = STATUS[s.status];
        const Icon = def.icon;
        const isSel = s.key === selected;
        return (
          <li key={s.key} className="relative pb-1">
            {i < steps.length - 1 && <span aria-hidden="true" className="absolute top-8 bottom-0 left-[19px] w-px bg-line" />}
            <button
              type="button"
              onClick={() => onSelect(s.key)}
              aria-current={isSel ? "step" : undefined}
              className={cn(
                "relative flex w-full cursor-pointer items-start gap-3 rounded-sm px-2 py-1.5 text-left hover:bg-surface-2",
                isSel && "bg-primary-soft hover:bg-primary-soft",
              )}
            >
              <span className={cn("mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full", def.bg, def.fg)}>
                <Icon className={cn("size-4", def.spin && "animate-spin motion-reduce:animate-none")} aria-hidden="true" />
              </span>
              <span className="min-w-0">
                <span className="block font-medium">
                  <span className="mr-1.5 text-muted tabular">{i + 1}.</span>
                  {s.title}
                </span>
                <span className={cn("block text-sm", def.fg)}>{s.statusLabel}</span>
                {s.meta && <span className="block truncate text-xs text-muted">{s.meta}</span>}
              </span>
            </button>
          </li>
        );
      })}
    </ol>
  );
}
