import { useMemo } from "react";
import { CornerDownRight } from "lucide-react";
import { cn } from "@/core/lib/cn";
import type { Tree } from "./api";
import { BOX_H, BOX_W, layoutTree } from "./layout";

/**
 * The guide as a left-to-right mind map. Every step is a button (Tab order follows the map); a branch that goes
 * back to a step drawn elsewhere is a small "→ step" box that selects that step.
 */
export function Mindmap({ tree, selected, onSelect, label }: { tree: Tree; selected: string | null; onSelect: (id: string) => void; label: string }) {
  const { boxes, links, width, height } = useMemo(() => layoutTree(tree), [tree]);
  const pad = 16;
  return (
    <div className="overflow-auto rounded-md border border-line bg-surface-2" tabIndex={-1}>
      <div role="group" aria-label={label} className="relative" style={{ width: width + pad * 2, height: height + pad * 2 }}>
        <svg className="pointer-events-none absolute inset-0" width={width + pad * 2} height={height + pad * 2} aria-hidden="true">
          {links.map((l) => {
            const x1 = l.from.x + pad, y1 = l.from.y + pad, x2 = l.to.x + pad, y2 = l.to.y + pad;
            const mx = (x1 + x2) / 2;
            return (
              <g key={l.key}>
                <path d={`M${x1},${y1} C${mx},${y1} ${mx},${y2} ${x2},${y2}`} fill="none" className={l.jump ? "stroke-muted" : "stroke-primary"} strokeWidth={1.5} strokeDasharray={l.jump ? "4 4" : undefined} />
              </g>
            );
          })}
        </svg>
        {/* Branch labels as text over the curves (readable in both themes). */}
        {links.filter((l) => l.label).map((l) => (
          <span key={`lbl-${l.key}`} className="absolute max-w-20 -translate-x-1/2 -translate-y-1/2 truncate rounded-xs bg-surface-2 px-1 text-xs text-muted" style={{ left: (l.from.x + l.to.x) / 2 + pad, top: (l.from.y + l.to.y) / 2 + pad }} title={l.label}>
            {l.label}
          </span>
        ))}
        {boxes.map((b) => {
          const node = tree.nodes[b.id];
          if (!node) return null;
          const on = selected === b.id;
          return b.jump ? (
            <button
              key={b.key}
              type="button"
              onClick={() => onSelect(b.id)}
              className="absolute flex items-center gap-1.5 rounded-sm border border-dashed border-line bg-surface px-2 text-left text-sm text-muted hover:text-ink"
              style={{ left: b.x + pad, top: b.y + pad + BOX_H / 4, width: BOX_W, height: BOX_H / 2 }}
              title={`Đi tới bước: ${node.title}`}
            >
              <CornerDownRight className="size-3.5 shrink-0" aria-hidden="true" />
              <span className="truncate">Đi tới: {node.title}</span>
            </button>
          ) : (
            <button
              key={b.key}
              type="button"
              aria-pressed={on}
              onClick={() => onSelect(b.id)}
              className={cn(
                "absolute rounded-sm border px-2.5 py-1.5 text-left text-sm leading-snug shadow-card transition-colors",
                on ? "border-primary bg-primary-soft text-ink ring-2 ring-primary/30" : "border-line bg-surface hover:border-primary",
                b.id === tree.root && "font-semibold",
              )}
              style={{ left: b.x + pad, top: b.y + pad, width: BOX_W, height: BOX_H }}
            >
              <span className="line-clamp-2">{node.title}</span>
              {node.note.trim() && <span className="sr-only"> – có ghi chú</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}
