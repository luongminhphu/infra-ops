import { Plus, X } from "lucide-react";
import { Button } from "@/core/ui/button";
import { Field, Input, Select } from "@/core/ui/field";
import { OP_LABEL, OUTCOME, type Fact, type Op, type Operand, type OutcomeResult, type StepNode } from "./api";
import { useTools } from "./Tools";

const OPS = Object.keys(OP_LABEL) as Op[];

function OperandEditor({ label, value, facts, onChange, allowValue }: { label: string; value: Operand; facts: Fact[]; onChange: (o: Operand) => void; allowValue: boolean }) {
  const fact = facts.find((f) => f.key === value.fact);
  return (
    <fieldset className="grid gap-2 rounded-sm border border-line p-2">
      <legend className="px-1 text-sm font-medium">{label}</legend>
      {allowValue && (
        <Select aria-label={`${label}: loại`} value={value.fact === null ? "value" : "fact"} onChange={(e) => onChange(e.target.value === "value" ? { fact: null, param: "", value: "" } : { fact: facts[0]?.key ?? "", param: "", value: null })}>
          <option value="fact">Dữ liệu về nhân viên</option>
          <option value="value">Giá trị cố định</option>
        </Select>
      )}
      {value.fact !== null ? (
        <>
          <Select aria-label={`${label}: dữ liệu`} value={value.fact} onChange={(e) => onChange({ ...value, fact: e.target.value, param: "" })}>
            {facts.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </Select>
          {fact?.param_label && (
            <Field label={fact.param_label} hint={fact.param_hint}>
              <Input value={value.param} maxLength={100} onChange={(e) => onChange({ ...value, param: e.target.value })} />
            </Field>
          )}
        </>
      ) : (
        <Field label="Giá trị">
          <Input value={value.value ?? ""} maxLength={100} onChange={(e) => onChange({ ...value, value: e.target.value })} placeholder="Ví dụ: 1 hoặc B5" />
        </Field>
      )}
    </fieldset>
  );
}

/** Kind-specific part of a step: the condition of a check, the classification of an outcome, the tools. */
export function StepKindEditor({ node, facts, onChange }: { node: StepNode; facts: Fact[]; onChange: (patch: Partial<StepNode>) => void }) {
  const kind = node.kind ?? "step";
  const tools = useTools();
  const setKind = (k: "step" | "check" | "outcome") =>
    onChange({
      kind: k,
      check: k === "check" ? node.check ?? { left: { fact: facts[0]?.key ?? "", param: "", value: null }, op: "<", right: { fact: null, param: "", value: "1" } } : null,
      outcome: k === "outcome" ? node.outcome ?? { result: "standard", approval: "" } : null,
      children: k === "check" ? node.children.slice(0, 2).map((b, i) => ({ ...b, label: i === 0 ? "Đúng" : "Sai" })) : node.children,
    });
  const actions = node.actions ?? [];
  const toolList = [...tools.entries()];
  return (
    <div className="grid gap-3">
      <Field label="Loại bước">
        <Select value={kind} onChange={(e) => setKind(e.target.value as "step" | "check" | "outcome")}>
          <option value="step">Bước hướng dẫn – người đọc và làm</option>
          <option value="check" disabled={!facts.length}>Kiểm tra – app tự so sánh dữ liệu</option>
          <option value="outcome">Kết luận – Tiêu chuẩn / Adhoc / Exception</option>
        </Select>
      </Field>
      {kind === "check" && node.check && (
        <div className="grid gap-2">
          <OperandEditor label="Vế trái" value={node.check.left} facts={facts} allowValue={false} onChange={(left) => onChange({ check: { ...node.check!, left } })} />
          <Field label="So sánh">
            <Select value={node.check.op} onChange={(e) => onChange({ check: { ...node.check!, op: e.target.value as Op } })}>
              {OPS.map((o) => <option key={o} value={o}>{OP_LABEL[o]}</option>)}
            </Select>
          </Field>
          <OperandEditor label="Vế phải" value={node.check.right} facts={facts} allowValue onChange={(right) => onChange({ check: { ...node.check!, right } })} />
          <p className="text-sm text-muted">Nhánh 1 = Đúng, nhánh 2 = Sai. Thiếu dữ liệu thì app dừng và báo “Chưa đủ dữ liệu”.</p>
        </div>
      )}
      {kind === "outcome" && node.outcome && (
        <div className="grid gap-2 sm:grid-cols-[10rem_1fr]">
          <Field label="Kết luận">
            <Select value={node.outcome.result} onChange={(e) => onChange({ outcome: { ...node.outcome!, result: e.target.value as OutcomeResult } })}>
              {(Object.keys(OUTCOME) as OutcomeResult[]).map((r) => <option key={r} value={r}>{OUTCOME[r].label}</option>)}
            </Select>
          </Field>
          <Field label="Ai duyệt / làm gì tiếp">
            <Input value={node.outcome.approval} maxLength={200} onChange={(e) => onChange({ outcome: { ...node.outcome!, approval: e.target.value } })} placeholder="Ví dụ: Function Head duyệt" />
          </Field>
        </div>
      )}
      <fieldset className="grid gap-2">
        <legend className="mb-1 text-sm font-medium">Công cụ mở từ bước này ({actions.length})</legend>
        {actions.map((a, i) => (
          <div key={i} className="grid grid-cols-[1.3fr_1fr_auto] items-end gap-2">
            <Field label={`Công cụ ${i + 1}`}>
              <Select value={a.tool} onChange={(e) => onChange({ actions: actions.map((x, j) => (j === i ? { ...x, tool: e.target.value } : x)) })}>
                {!tools.has(a.tool) && <option value={a.tool}>{a.tool} (không có quyền / đã gỡ)</option>}
                {toolList.map(([k, t]) => <option key={k} value={k}>{t.module} › {t.label}</option>)}
              </Select>
            </Field>
            <Field label="Tên nút">
              <Input value={a.label} maxLength={60} placeholder={tools.get(a.tool)?.label} onChange={(e) => onChange({ actions: actions.map((x, j) => (j === i ? { ...x, label: e.target.value } : x)) })} />
            </Field>
            <Button variant="ghost" aria-label={`Bỏ công cụ ${i + 1}`} onClick={() => onChange({ actions: actions.filter((_, j) => j !== i) })}><X className="size-4" aria-hidden="true" /></Button>
          </div>
        ))}
        <span>
          <Button size="sm" disabled={!toolList.length || actions.length >= 6} onClick={() => onChange({ actions: [...actions, { tool: toolList[0]![0], label: "" }] })}>
            <Plus className="size-4" aria-hidden="true" />Thêm công cụ
          </Button>
        </span>
      </fieldset>
    </div>
  );
}
