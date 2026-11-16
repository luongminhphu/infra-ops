import { cn } from "@/core/lib/cn";
import { STATUS, type Status } from "./status";

/**
 * The signature element of infra-ops: a fixed sequence of system cells per employee.
 * Same order, same position everywhere (reconciliation row, job header, dashboard).
 */
export interface RailCell {
  key: string;
  label: string;
  status: Status;
  /** Short value shown inside the cell (e.g. "3" assets). */
  value?: string;
  detail?: string;
}

export function SystemRail({ cells, compact = false, className }: { cells: RailCell[]; compact?: boolean; className?: string }) {
  return (
    <ol className={cn("flex items-stretch gap-px overflow-hidden rounded-xs bg-line", className)} aria-label="Trạng thái theo hệ thống">
      {cells.map((cell) => {
        const def = STATUS[cell.status];
        const Icon = def.icon;
        return (
          <li
            key={cell.key}
            className={cn("flex min-w-0 flex-1 items-center gap-1.5 bg-surface", compact ? "px-1.5 py-1" : "px-2 py-1.5")}
            title={cell.detail ?? `${cell.label}: ${def.label}`}
          >
            <span className={cn("flex size-5 shrink-0 items-center justify-center rounded-xs", def.bg, def.fg)}>
              <Icon className="size-3.5" aria-hidden="true" />
            </span>
            {!compact && <span className="truncate text-xs text-muted">{cell.label}</span>}
            {cell.value && <span className="tabular ml-auto text-xs font-medium">{cell.value}</span>}
            <span className="sr-only">
              {cell.label}: {def.label}
              {cell.detail ? ` – ${cell.detail}` : ""}
            </span>
          </li>
        );
      })}
    </ol>
  );
}
