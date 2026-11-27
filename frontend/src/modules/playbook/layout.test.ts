import { describe, expect, it } from "vitest";
import { layoutTree } from "./layout";
import { bypassStep, moveStep, pruneUnreachable, removeStep, unreachableSteps, type Tree } from "./api";

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

  it("removes a middle step and joins the steps around it", () => {
    const chain: Tree = {
      root: "a",
      nodes: {
        a: { title: "A", note: "", children: [{ label: "Đủ", to: "b" }, { label: "Thiếu", to: "x" }] },
        b: { title: "Kiểm tra laptop", note: "", children: [{ label: "", to: "c" }] },
        c: { title: "C", note: "", children: [{ label: "", to: "d" }] },
        d: { title: "D", note: "", children: [] },
        x: { title: "X", note: "", children: [{ label: "Đã bổ sung", to: "b" }] },
      },
    };
    const t = bypassStep(chain, "b")!;
    expect(Object.keys(t.nodes).sort()).toEqual(["a", "c", "d", "x"]);
    expect(t.nodes.a!.children).toEqual([{ label: "Đủ", to: "c" }, { label: "Thiếu", to: "x" }]);
    expect(t.nodes.x!.children).toEqual([{ label: "Đã bổ sung", to: "c" }]);
    // A branching step, the root, or a step whose next step is the one before it: no bypass.
    expect(bypassStep(chain, "a")).toBeNull();
    expect(bypassStep(tree, "a")).toBeNull();
    const back: Tree = { root: "a", nodes: { a: { title: "A", note: "", children: [{ label: "", to: "b" }] },
      b: { title: "B", note: "", children: [{ label: "", to: "a" }] } } };
    expect(Object.keys(bypassStep(back, "b")!.nodes)).toEqual(["a"]); // a → a is dropped, not a self-loop
  });

  it("drag and drop moves a step with the steps after it behind another step", () => {
    const t: Tree = {
      root: "a",
      nodes: {
        a: { title: "A", note: "", children: [{ label: "", to: "b" }, { label: "", to: "c" }] },
        b: { title: "B", note: "", children: [{ label: "", to: "d" }] },
        c: { title: "C", note: "", children: [] },
        d: { title: "D", note: "", children: [{ label: "Lại", to: "a" }] },
        k: { title: "K", note: "", kind: "check", children: [] } as Tree["nodes"][string],
      },
    };
    const moved = moveStep(t, "b", "c") as Tree;
    expect(moved.nodes.a!.children.map((x) => x.to)).toEqual(["c"]);
    expect(moved.nodes.c!.children).toEqual([{ label: "", to: "b" }]);
    expect(moved.nodes.b!.children).toEqual([{ label: "", to: "d" }]); // the steps after it come along
    expect(unreachableSteps(moved)).toEqual(unreachableSteps(t));
    expect(moveStep(t, "b", "d")).toBe("Không thả được vào chính các bước phía sau nó.");
    expect(moveStep(t, "a", "c")).toBe("Không di chuyển được bước gốc.");
    expect(moveStep(t, "b", "a")).toBe("Bước này đã nối sau bước đó.");
    const out = { ...t, nodes: { ...t.nodes, c: { ...t.nodes.c!, kind: "outcome" as const } } };
    expect(typeof moveStep(out, "b", "c")).toBe("string");
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
