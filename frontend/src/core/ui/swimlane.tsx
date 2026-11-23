import { ArrowRightLeft, Bot, Hand, UserRound } from "lucide-react";
import { cn } from "@/core/lib/cn";
import { formatDateTime } from "@/core/lib/format";
import { STATUS, type Status } from "./status";

export interface LaneStep {
  key: string;
  title: string;
  kind: "auto" | "manual";
  status: Status;
  statusLabel: string;
  current: boolean;
}

export interface LaneHandoff {
  from_name: string | null;
  to_name: string;
  step_key: string | null;
  source: "sdp" | "app";
  reason: string;
  at: string;
}

const NOBODY = "Chưa có người phụ trách";

/**
 * Who owns each step: the latest hand-over made at or before that step (pure, tested).
 * A hand-over recorded while the job sat on step N moves step N and everything after it.
 */
export function laneLayout(steps: Pick<LaneStep, "key">[], handoffs: LaneHandoff[]) {
  const idx = (h: LaneHandoff) => (h.step_key ? steps.findIndex((s) => s.key === h.step_key) : -1);
  const sorted = [...handoffs].sort((a, b) => a.at.localeCompare(b.at));
  const owners = steps.map((_, i) => {
    const last = sorted.filter((h) => idx(h) <= i).at(-1);
    return last?.to_name ?? NOBODY;
  });
  const lanes = [...new Set(owners)];
  // The hand-over that moved step i into a new lane (shown on the connector into step i).
  const changes = steps.map((_, i) =>
    i > 0 && owners[i] !== owners[i - 1] ? (sorted.filter((h) => idx(h) <= i && idx(h) > i - 1).at(-1) ?? sorted.filter((h) => idx(h) <= i).at(-1) ?? null) : null,
  );
  return { owners, lanes, changes };
}

const COL = 184;
const ROW = 112;
const HEAD = 168;
const NODE_W = 156;
const NODE_H = 76;

const initials = (name: string) =>
  name === NOBODY ? "?" : name.split(/\s+/).filter(Boolean).slice(-2).map((p) => p[0]!.toUpperCase()).join("");

/**
 * Swimlane of a job: one lane per person, steps left to right, the current step glowing and every
 * change of hands drawn as a connector jumping lanes, labelled with who/when.
 */
export function Swimlane({ steps, handoffs, label }: { steps: LaneStep[]; handoffs: LaneHandoff[]; label: string }) {
  const { owners, lanes, changes } = laneLayout(steps, handoffs);
  const width = HEAD + steps.length * COL;
  const height = lanes.length * ROW;
  const pos = (i: number) => ({ x: HEAD + i * COL + (COL - NODE_W) / 2, y: lanes.indexOf(owners[i]!) * ROW + (ROW - NODE_H) / 2 });
  const currentIdx = steps.findIndex((s) => s.current);

  return (
    <figure>
      {/* Text version for screen readers and print; the drawing below repeats it visually. */}
      <figcaption className="sr-only">
        {label}.{" "}
        {steps.map((s, i) => `Bước ${i + 1} ${s.title}: ${s.statusLabel}, phụ trách ${owners[i]}.`).join(" ")}
      </figcaption>
      <div className="overflow-x-auto rounded-md border border-line" tabIndex={0} role="region" aria-label={`${label} (cuộn ngang)`}>
        <div className="relative" style={{ width, height }} aria-hidden="true">
          {lanes.map((lane, li) => (
            <div key={lane} className={cn("absolute inset-x-0 border-b border-line", li % 2 ? "bg-surface" : "bg-surface-2/60")} style={{ top: li * ROW, height: ROW }}>
              <div className="sticky left-0 flex h-full items-center gap-2 border-r border-line bg-inherit px-3" style={{ width: HEAD }}>
                <span className={cn("flex size-8 shrink-0 items-center justify-center rounded-full text-xs font-semibold", lane === NOBODY ? "bg-surface-2 text-muted" : "bg-primary-soft text-primary")}>
                  {lane === NOBODY ? <UserRound className="size-4" /> : initials(lane)}
                </span>
                <span className="line-clamp-2 text-sm font-medium">{lane}</span>
              </div>
            </div>
          ))}

          <svg className="pointer-events-none absolute inset-0" width={width} height={height}>
            <defs>
              <marker id="sl-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto">
                <path d="M0 0 L8 4 L0 8 z" fill="var(--line-strong)" />
              </marker>
              <marker id="sl-arrow-hand" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="8" markerHeight="8" orient="auto">
                <path d="M0 0 L8 4 L0 8 z" fill="var(--primary)" />
              </marker>
            </defs>
            {steps.slice(1).map((_, k) => {
              const i = k + 1;
              const a = pos(i - 1);
              const b = pos(i);
              const x1 = a.x + NODE_W;
              const y1 = a.y + NODE_H / 2;
              const x2 = b.x;
              const y2 = b.y + NODE_H / 2;
              const hand = owners[i] !== owners[i - 1];
              const mx = (x1 + x2) / 2;
              const towardCurrent = i === currentIdx;
              return (
                <path
                  key={i}
                  d={hand ? `M${x1} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2 - 2} ${y2}` : `M${x1} ${y1} L ${x2 - 2} ${y2}`}
                  fill="none"
                  stroke={hand ? "var(--primary)" : "var(--line-strong)"}
                  strokeWidth={hand ? 2 : 1.5}
                  strokeDasharray={hand || towardCurrent ? "6 5" : undefined}
                  className={towardCurrent ? "swimlane-flow" : undefined}
                  markerEnd={`url(#${hand ? "sl-arrow-hand" : "sl-arrow"})`}
                />
              );
            })}
          </svg>

          {changes.map((h, i) => {
            if (!h) return null;
            const a = pos(i - 1);
            const b = pos(i);
            return (
              <div
                key={`h${i}`}
                className="absolute z-10 flex -translate-x-1/2 -translate-y-1/2 items-center gap-1 rounded-full border border-primary bg-surface px-2 py-0.5 text-[11px] whitespace-nowrap text-primary shadow-card"
                style={{ left: (a.x + NODE_W + b.x) / 2, top: (a.y + b.y) / 2 + NODE_H / 2 }}
              >
                <ArrowRightLeft className="size-3" />
                {formatDateTime(h.at)} · {h.source === "sdp" ? "trên SDP" : "infra-ops"}
              </div>
            );
          })}

          {steps.map((s, i) => {
            const p = pos(i);
            const def = STATUS[s.status];
            const Icon = def.icon;
            return (
              <div key={s.key} className="absolute" style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H }}>
                {s.current && <span className="absolute -inset-1 rounded-lg bg-primary/25 motion-safe:animate-pulse" />}
                <div
                  className={cn(
                    "relative flex h-full flex-col justify-between rounded-md border bg-surface px-2.5 py-1.5 shadow-card",
                    s.current ? "border-2 border-primary" : s.status === "pending" ? "border-dashed border-line-strong" : "border-line",
                  )}
                >
                  <span className="line-clamp-2 shrink-0 text-[13px] leading-snug font-medium" title={s.title}>
                    <span className="mr-1 text-muted tabular">{i + 1}.</span>
                    {s.title}
                  </span>
                  <span className="flex items-center justify-between gap-1 text-[11px]">
                    <span className={cn("inline-flex items-center gap-1", def.fg)}>
                      <Icon className={cn("size-3.5", def.spin && "motion-safe:animate-spin")} />
                      {s.statusLabel}
                    </span>
                    {s.kind === "auto" ? <Bot className="size-3.5 text-muted" /> : <Hand className="size-3.5 text-muted" />}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      </div>
      {handoffs.length > 0 && (
        <div className="mt-3">
          <h3 className="text-sm font-semibold">Lịch sử phụ trách</h3>
          <ol className="mt-1 space-y-1 text-sm">
            {handoffs.map((h, i) => (
              <li key={i} className="flex flex-wrap gap-x-2">
                <span className="text-muted tabular">{formatDateTime(h.at)}</span>
                <span>
                  {h.from_name ? <>Chuyển từ <strong>{h.from_name}</strong> sang <strong>{h.to_name}</strong></> : <>Giao cho <strong>{h.to_name}</strong></>}
                  {" "}({h.source === "sdp" ? "trên SDP" : "trong infra-ops"}
                  {h.step_key ? `, tại bước ${steps.findIndex((s) => s.key === h.step_key) + 1}` : ", trước khi bắt đầu"})
                  {h.reason && <span className="text-muted"> – {h.reason}</span>}
                </span>
              </li>
            ))}
          </ol>
        </div>
      )}
    </figure>
  );
}
