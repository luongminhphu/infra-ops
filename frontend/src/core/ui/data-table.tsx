import { useState, type ReactNode } from "react";
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from "@tanstack/react-table";
import { ArrowDown, ArrowUp, ArrowUpDown } from "lucide-react";
import { cn } from "@/core/lib/cn";

export type Density = "compact" | "comfortable";

interface DataTableProps<T> {
  data: T[];
  columns: ColumnDef<T, unknown>[];
  caption: string;
  getRowId: (row: T) => string;
  onRowClick?: (row: T) => void;
  selectedId?: string | null;
  density?: Density;
  empty?: ReactNode;
  maxHeight?: string;
}

/**
 * Sortable table with sticky header. Sort controls are <button>s inside <th>, the sorted
 * column alone carries aria-sort, and rows keep stable keys so React moves (not rebuilds) them.
 * Row activation uses a real button in the first cell for keyboard users.
 */
export function DataTable<T>({
  data,
  columns,
  caption,
  getRowId,
  onRowClick,
  selectedId,
  density = "compact",
  empty,
  maxHeight = "calc(100dvh - 230px)",
}: DataTableProps<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const table = useReactTable({
    data,
    columns,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId,
  });
  const rows = table.getRowModel().rows;
  return (
    <div className="overflow-auto rounded-md border border-line bg-surface shadow-card" style={{ maxHeight }}>
      <table className="w-full border-collapse text-left">
        <caption className="sr-only">{caption}</caption>
        <thead className="sticky top-0 z-10 bg-surface-2">
          {table.getHeaderGroups().map((hg) => (
            <tr key={hg.id}>
              {hg.headers.map((header) => {
                const sorted = header.column.getIsSorted();
                const canSort = header.column.getCanSort();
                const label = flexRender(header.column.columnDef.header, header.getContext());
                return (
                  <th
                    key={header.id}
                    scope="col"
                    aria-sort={sorted === "asc" ? "ascending" : sorted === "desc" ? "descending" : undefined}
                    className="border-b border-line px-3 py-2 text-sm font-semibold whitespace-nowrap text-muted"
                    style={{ width: header.getSize() !== 150 ? header.getSize() : undefined }}
                  >
                    {canSort ? (
                      <button
                        type="button"
                        className="inline-flex cursor-pointer items-center gap-1 hover:text-ink"
                        onClick={header.column.getToggleSortingHandler()}
                      >
                        {label}
                        {sorted === "asc" ? (
                          <ArrowUp className="size-3.5" aria-hidden="true" />
                        ) : sorted === "desc" ? (
                          <ArrowDown className="size-3.5" aria-hidden="true" />
                        ) : (
                          <ArrowUpDown className="size-3.5 opacity-40" aria-hidden="true" />
                        )}
                      </button>
                    ) : (
                      label
                    )}
                  </th>
                );
              })}
            </tr>
          ))}
        </thead>
        <tbody>
          {rows.map((row) => (
            <tr
              key={row.id}
              onClick={onRowClick ? () => onRowClick(row.original) : undefined}
              aria-selected={onRowClick ? row.id === selectedId : undefined}
              className={cn(
                "border-b border-line last:border-b-0",
                onRowClick && "cursor-pointer hover:bg-surface-2",
                row.id === selectedId && "bg-primary-soft hover:bg-primary-soft",
              )}
              style={{ height: density === "compact" ? "var(--row-h)" : "var(--row-h-comfortable)" }}
            >
              {row.getVisibleCells().map((cell) => (
                <td key={cell.id} className="px-3 py-1 align-middle text-base">
                  {flexRender(cell.column.columnDef.cell, cell.getContext())}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      {rows.length === 0 && empty}
    </div>
  );
}
