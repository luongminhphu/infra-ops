import { describe, expect, it } from "vitest";
import { layoutTree } from "./layout";
import { pruneUnreachable, removeStep, unreachableSteps, type Tree } from "./api";

const tree: Tree = {
  root: "a",
  nodes: {
    a: { title: "Mất mạng", note: "", children: [{ label: "Đèn sáng", to: "b" }, { label: "Đèn tắt", to: "c" }] },
    b: { title: "Kiểm tra IP", note: "", children: [{ label: "Chưa được", to: "c" }] },
    c: { title: "Thay cáp", note: "", children: [] },
  },
};

describe("mind map layout", () => {
  it("draws each step once and other branches to it as jumps; parents centred on children", () => {
    const { boxes, links } = layoutTree(tree);
    const real = boxes.filter((b) => !b.jump).map((b) => b.id).sort();
    expect(real).toEqual(["a", "b", "c"]);
    const jump = boxes.filter((b) => b.jump);
    expect(jump.map((b) => b.id)).toEqual(["c"]); // b → c is drawn as a jump back to c under a
    const a = boxes.find((b) => b.key === "r")!;
    const kids = boxes.filter((b) => b.key === "r/0" || b.key === "r/1");
    expect(a.y).toBe((kids[0]!.y + kids[1]!.y) / 2);
    expect(links.find((l) => l.jump)?.label).toBe("Chưa được");
  });

  it("lists and prunes steps no branch leads to", () => {
    const t = { ...tree, nodes: { ...tree.nodes, a: { ...tree.nodes.a!, children: [] } } };
    expect(unreachableSteps(t).length).toBeGreaterThan(0);
    expect(unreachableSteps(pruneUnreachable(t))).toEqual([]);
    expect(Object.keys(pruneUnreachable(t).nodes)).toEqual(["a"]);
  });

  it("removing a step drops branches to it and steps left unreachable", () => {
    const t = removeStep({ ...tree, nodes: { ...tree.nodes, a: { ...tree.nodes.a!, children: [{ label: "", to: "b" }] } } }, "b");
    expect(Object.keys(t.nodes)).toEqual(["a"]);
  });
});
