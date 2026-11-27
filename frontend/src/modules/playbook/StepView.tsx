import { CornerDownRight } from "lucide-react";
import { StatusBadge } from "@/core/ui/status";
import { OP_LABEL, OUTCOME, type Fact, type Operand, type StepNode, type Tree } from "./api";
import { JobStepInfo } from "./JobStep";
import { Note } from "./Note";
import { StepActions } from "./Tools";

export function operandText(o: Operand, facts: Fact[]): string {
  if (o.fact === null) return `“${o.value ?? ""}”`;
  const f = facts.find((x) => x.key === o.fact);
  return `${f?.label ?? o.fact}${o.param ? ` (${o.param})` : ""}`;
}

/** A step as a reader sees it: what kind of step, the note, the tools it opens and where it leads. */
export function StepView({ tree, node, facts, onSelect }: { tree: Tree; node: StepNode; facts: Fact[]; onSelect: (id: string) => void }) {
  const kind = node.kind ?? "step";
  return (
    <div>
      {kind === "check" && node.check && (
        <p className="mb-3 rounded-sm bg-surface-2 px-3 py-2 text-sm">
          <span className="font-medium">App tự kiểm tra:</span> {operandText(node.check.left, facts)} {OP_LABEL[node.check.op]} {operandText(node.check.right, facts)}
        </p>
      )}
      {kind === "outcome" && node.outcome && (
        <p className="mb-3 flex flex-wrap items-center gap-2">
          <StatusBadge status={OUTCOME[node.outcome.result].status} label={`Kết luận: ${OUTCOME[node.outcome.result].label}`} />
          {node.outcome.approval && <span className="text-sm">{node.outcome.approval}</span>}
        </p>
      )}
      {kind === "job" && node.job && <JobStepInfo job={node.job} />}
      <Note text={node.note} />
      <StepActions node={node} />
      {node.children.length > 0 && (
        <div className="mt-4 border-t border-line pt-3">
          <p className="mb-1.5 text-sm font-medium text-muted">Bước tiếp theo</p>
          <ul className="space-y-1">
            {node.children.map((b, i) => (
              <li key={i}>
                <button type="button" onClick={() => onSelect(b.to)} className="inline-flex min-h-8 items-center gap-1.5 text-left text-primary hover:underline">
                  <CornerDownRight className="size-4 shrink-0" aria-hidden="true" />
                  {(kind === "check" ? (i === 0 ? "Đúng" : "Sai") : b.label) ? <><b className="font-medium">{kind === "check" ? (i === 0 ? "Đúng" : "Sai") : b.label}</b> → </> : null}{tree.nodes[b.to]?.title}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}
