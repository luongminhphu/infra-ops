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

/** Remove a step, every branch to it, and the steps that then hang nowhere. */
export function removeStep(tree: Tree, id: string): Tree {
  const nodes: Record<string, StepNode> = {};
  for (const [k, n] of Object.entries(tree.nodes)) {
    if (k !== id) nodes[k] = { ...n, children: n.children.filter((b) => b.to !== id) };
  }
  const next = { root: tree.root, nodes };
  const keep = reachable(next);
  return { root: tree.root, nodes: Object.fromEntries(Object.entries(nodes).filter(([k]) => keep.has(k))) };
}
