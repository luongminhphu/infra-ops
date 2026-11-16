import { STATUS } from "@/core/ui/status";
import { cn } from "@/core/lib/cn";
import type { Cell } from "./api";
import { CELL_STATUS } from "./labels";

/** One system cell in the reconciliation grid. A cell with an open issue is shown as "Lệch". */
export function CellChip({ cell, flagged, system }: { cell: Cell; flagged: boolean; system: string }) {
  const status = flagged ? "mismatch" : CELL_STATUS[cell.status];
  const def = STATUS[status];
  const Icon = def.icon;
  return (
    <span
      className={cn(
        "inline-flex h-6 max-w-full items-center gap-1 rounded-xs px-1.5 text-sm whitespace-nowrap",
        def.bg,
        def.fg,
        flagged && "font-medium ring-1 ring-mismatch/40",
      )}
      title={cell.detail || undefined}
    >
      <Icon className="size-3.5 shrink-0" aria-hidden="true" />
      <span className="truncate">{cell.label}</span>
      <span className="sr-only">
        {" "}
        – {system}: {flagged ? "Lệch" : def.label}
      </span>
    </span>
  );
}
