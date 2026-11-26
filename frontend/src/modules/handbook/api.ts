import { api } from "@/core/api/client";

export interface Branch {
  /** The choice that leads to the step ("Có", "Không", "Máy Windows"…). */
  label: string;
  to: string;
}
export interface StepNode {
  title: string;
  /** Light markdown: **bold**, `code`, lists, [links](url), ![images](url). */
  note: string;
  children: Branch[];
}
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
  api<GuideDetail>(`/api/handbook/guides/${id}?version=${encodeURIComponent(version)}`);

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
