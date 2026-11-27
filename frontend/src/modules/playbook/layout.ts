import { drawnParents, type Tree } from "./api";

export const BOX_W = 200;
export const BOX_H = 60;
const COL = BOX_W + 70;
const ROW = BOX_H + 22;

export interface Box {
  key: string;
  /** Step shown here; for a jump, the step it points to. */
  id: string;
  jump: boolean;
  x: number;
  y: number;
}
export interface Link {
  key: string;
  from: { x: number; y: number };
  to: { x: number; y: number };
  label: string;
  jump: boolean;
}

/**
 * Left-to-right mind map. Each step is drawn once, under the parent that reaches it first (breadth first);
 * every other branch to it is a small "jump" box pointing back to it. Leaves take one row each, parents sit in
 * the middle of their children.
 */
export function layoutTree(tree: Tree): { boxes: Box[]; links: Link[]; width: number; height: number } {
  const parent = drawnParents(tree);
  const boxes: Box[] = [];
  const links: Link[] = [];
  let row = 0;
  let maxDepth = 0;
  const place = (id: string, depth: number, key: string, jump: boolean): number => {
    maxDepth = Math.max(maxDepth, depth);
    const x = depth * COL;
    if (jump) {
      const y = row++ * ROW;
      boxes.push({ key, id, jump: true, x, y });
      return y;
    }
    const ys: number[] = [];
    (tree.nodes[id]?.children ?? []).forEach((b, i) => {
      if (!tree.nodes[b.to]) return;
      const own = parent.get(b.to) === id;
      const childKey = `${key}/${i}`;
      const cy = place(b.to, depth + 1, childKey, !own);
      ys.push(cy);
      links.push({ key: childKey, from: { x: x + BOX_W, y: 0 }, to: { x: (depth + 1) * COL, y: cy + BOX_H / 2 }, label: b.label, jump: !own });
    });
    const y = ys.length ? (ys[0]! + ys[ys.length - 1]!) / 2 : row++ * ROW;
    boxes.push({ key, id, jump: false, x, y });
    for (const l of links) if (l.key.startsWith(`${key}/`) && l.key.split("/").length === key.split("/").length + 1) l.from.y = y + BOX_H / 2;
    return y;
  };
  place(tree.root, 0, "r", false);
  const height = Math.max(row, 1) * ROW;
  return { boxes, links, width: (maxDepth + 1) * COL - (COL - BOX_W), height };
}
