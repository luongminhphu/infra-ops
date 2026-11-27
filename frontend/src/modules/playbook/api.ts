import { api } from "@/core/api/client";

export interface Branch {
  /** The choice that leads to the step ("Có", "Không", "Máy Windows"…). */
  label: string;
  to: string;
}
export interface Operand {
  fact: string | null;
  param: string;
  value: string | null;
}
export type Op = ">=" | ">" | "<=" | "<" | "==" | "!=";
export type OutcomeResult = "standard" | "adhoc" | "exception";
export interface StepNode {
  /** step: read and act; check: the app tests a condition (branch 1 = Đúng, 2 = Sai); outcome: classification. */
  kind?: "step" | "check" | "outcome";
  title: string;
  /** Light markdown: **bold**, `code`, lists, [links](url), ![images](url). */
  note: string;
  children: Branch[];
  /** Tools the step opens: a page key ("lifecycle.jobs" = /lifecycle/jobs). */
  actions?: { tool: string; label: string }[];
  check?: { left: Operand; op: Op; right: Operand } | null;
  outcome?: { result: OutcomeResult; approval: string } | null;
}
export interface Fact {
  key: string;
  label: string;
  kind: "number" | "text";
  param_label: string | null;
  param_hint: string;
}
export interface TraceValue {
  label: string;
  value: string | number;
  source: string;
}
export interface TraceEntry {
  id: string;
  kind: "step" | "check" | "outcome";
  title: string;
  left?: TraceValue | null;
  right?: TraceValue | null;
  op?: Op;
  result?: boolean;
  missing?: string;
  options?: { index: number; label: string; to: string }[];
  chosen?: number;
}
export interface RunResult {
  status: "outcome" | "needs_choice" | "missing_data" | "end" | "loop";
  trace: TraceEntry[];
  outcome?: { result: OutcomeResult; approval: string; label: string };
  employee_id: string;
}

export const OP_LABEL: Record<Op, string> = { ">=": "≥", ">": ">", "<=": "≤", "<": "<", "==": "bằng", "!=": "khác" };
export const OUTCOME: Record<OutcomeResult, { label: string; status: "done" | "warning" | "failed" }> = {
  standard: { label: "Tiêu chuẩn", status: "done" },
  adhoc: { label: "Adhoc", status: "warning" },
  exception: { label: "Exception", status: "failed" },
};

/** A tool key is a page path without slashes: "lifecycle.offboard-review" = /lifecycle/offboard-review. */
export const toolKey = (path: string) => path.replace(/^\//, "").replaceAll("/", ".");
export interface Tree {
  root: string;
  nodes: Record<string, StepNode>;
}
export interface GuideSummary {
  id: string;
  title: string;
  category: string;
  description: string;
  published_version: number | null;
  draft_updated_at?: string;
  draft_updated_by?: string | null;
}
export interface GuideDetail extends GuideSummary {
  version: number | null;
  tree: Tree;
  published_at?: string;
  published_by?: string | null;
}
export interface GuideVersionInfo {
  version: number;
  note: string;
  published_at: string;
  published_by: string | null;
  steps: number;
}

export const getGuide = (id: string, version = "published") =>
  api<GuideDetail>(`/api/playbook/guides/${id}?version=${encodeURIComponent(version)}`);

export function newId(): string {
  return Math.random().toString(36).slice(2, 10);
}

/** Steps no branch leads to from the root (removed together with the step that held them). */
export function reachable(tree: Tree): Set<string> {
  const seen = new Set([tree.root]);
  const stack = [tree.root];
  while (stack.length) {
    for (const b of tree.nodes[stack.pop()!]?.children ?? []) {
      if (!seen.has(b.to) && tree.nodes[b.to]) {
        seen.add(b.to);
        stack.push(b.to);
      }
    }
  }
  return seen;
}

/** Steps no branch leads to any more (e.g. a branch was pointed past them); they are not drawn on the map. */
export function unreachableSteps(tree: Tree): string[] {
  const keep = reachable(tree);
  return Object.keys(tree.nodes).filter((k) => !keep.has(k));
}

/**
 * Remove a step in the middle and join the steps around it: every branch to it now leads to its one next step
 * (keeping the branch's own label), so nothing after it is lost. Only for a step with exactly one next step –
 * a step that branches cannot say which way the earlier step should go. Returns null when that does not apply.
 */
export function bypassStep(tree: Tree, id: string): Tree | null {
  const node = tree.nodes[id];
  if (!node || id === tree.root || node.children.length !== 1) return null;
  const next = node.children[0]!.to;
  if (next === id) return null;
  const nodes: Record<string, StepNode> = {};
  for (const [k, n] of Object.entries(tree.nodes)) {
    if (k === id) continue;
    const seen = new Set<string>();
    const children = n.children
      .map((b) => (b.to === id ? { ...b, to: next } : b))
      .filter((b) => b.to !== k && !seen.has(`${b.label}\u0000${b.to}`) && seen.add(`${b.label}\u0000${b.to}`));
    nodes[k] = { ...n, children };
  }
  return pruneUnreachable({ root: tree.root, nodes });
}

/** The step each step is drawn under: the first that reaches it, breadth first (the root has ""). */
export function drawnParents(tree: Tree): Map<string, string> {
  const parent = new Map<string, string>([[tree.root, ""]]);
  const queue = [tree.root];
  while (queue.length) {
    const id = queue.shift()!;
    for (const b of tree.nodes[id]?.children ?? []) {
      if (!parent.has(b.to) && tree.nodes[b.to]) {
        parent.set(b.to, id);
        queue.push(b.to);
      }
    }
  }
  return parent;
}

/**
 * Drag and drop on the map: move a step – with the steps after it – to follow `target`. The branch from the step
 * it is drawn under goes; `target` gets a new branch to it (other branches to the step, e.g. jumps back, stay).
 * Returns the reason in Vietnamese when the move is not possible.
 */
export function moveStep(tree: Tree, id: string, target: string): Tree | string {
  const node = tree.nodes[id];
  const to = tree.nodes[target];
  if (!node || !to || id === target) return "Thả lên một bước khác.";
  if (id === tree.root) return "Không di chuyển được bước gốc.";
  const from = drawnParents(tree).get(id) ?? "";
  if (from === target) return "Bước này đã nối sau bước đó.";
  if (to.kind === "outcome") return "Bước kết luận là bước cuối – không nối thêm bước sau.";
  if (to.kind === "check" && to.children.length >= 2) return "Bước kiểm tra đã đủ nhánh Đúng và Sai.";
  // The target must still be reachable without the moved step, or the branch would hang in the moved part.
  const without = reachable({ root: tree.root, nodes: Object.fromEntries(Object.entries(tree.nodes).filter(([k]) => k !== id)) });
  if (!without.has(target)) return "Không thả được vào chính các bước phía sau nó.";
  const nodes: Record<string, StepNode> = { ...tree.nodes };
  if (from) nodes[from] = { ...nodes[from]!, children: nodes[from]!.children.filter((b) => b.to !== id) };
  const label = to.kind === "check" ? (to.children.length ? "Sai" : "Đúng") : "";
  nodes[target] = { ...to, children: [...to.children, { label, to: id }] };
  return { root: tree.root, nodes };
}

/** Drop the steps that hang nowhere. */
export function pruneUnreachable(tree: Tree): Tree {
  const keep = reachable(tree);
  return { root: tree.root, nodes: Object.fromEntries(Object.entries(tree.nodes).filter(([k]) => keep.has(k))) };
}

/** Remove a step, every branch to it, and the steps that then hang nowhere. */
export function removeStep(tree: Tree, id: string): Tree {
  const nodes: Record<string, StepNode> = {};
  for (const [k, n] of Object.entries(tree.nodes)) {
    if (k !== id) nodes[k] = { ...n, children: n.children.filter((b) => b.to !== id) };
  }
  return pruneUnreachable({ root: tree.root, nodes });
}
