import { useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { CircleAlert, CornerDownRight, Play, RotateCcw } from "lucide-react";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { Field, Input } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import { OP_LABEL, OUTCOME, type RunResult, type TraceValue, type Tree } from "./api";

const show = (v: TraceValue | null | undefined) => (v ? `${v.value}` : "—");

/**
 * "Chạy với Mã NV": the app follows the guide for one employee – check steps are decided from the data the
 * modules know (with where each value came from), and it stops where a person must choose or data is missing.
 * Read only.
 */
export function RunPanel({ guideId, tree, version, onPath, onSelect }: {
  guideId: string; tree: Tree; version: "published" | "draft"; onPath: (ids: Set<string>) => void; onSelect: (id: string) => void;
}) {
  const [emp, setEmp] = useState("");
  const [choices, setChoices] = useState<Record<string, number>>({});
  const [touched, setTouched] = useState(false);
  const run = useMutation({
    mutationFn: (c: Record<string, number>) => api<RunResult>(`/api/playbook/guides/${guideId}/run`, { method: "POST", json: { employee_id: emp.trim(), version, choices: c } }),
    onSuccess: (r) => {
      onPath(new Set(r.trace.map((t) => t.id)));
      onSelect(r.trace.at(-1)!.id);
    },
  });
  const valid = /^[0-9A-Za-z]{1,20}$/.test(emp.trim());
  const go = (c: Record<string, number>) => { setTouched(true); if (valid) { setChoices(c); run.mutate(c); } };
  const r = run.data;
  const last = r?.trace.at(-1);
  return (
    <section aria-label="Chạy với Mã NV" className="rounded-md border border-line bg-surface p-4">
      <h2 className="flex items-center gap-1.5 text-base font-semibold">
        Chạy với Mã NV
        <HelpTip title="Chạy với Mã NV">
          <span className="block">App đi theo sơ đồ cho một nhân viên: ở bước kiểm tra, app tự so sánh dữ liệu (tài sản đang dùng, band, tiêu chuẩn cấp phát…) và ghi nguồn của từng giá trị. Gặp bước cần người chọn thì dừng để bạn chọn nhánh.</span>
          <span className="block">Chỉ đọc dữ liệu từ lần đồng bộ gần nhất, không thay đổi gì.</span>
        </HelpTip>
      </h2>
      <form className="mt-2 flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); go({}); }}>
        <Field label="Mã NV" error={valid ? "" : "Nhập Mã NV (chữ và số)."} showError={touched} className="w-40">
          <Input value={emp} onChange={(e) => setEmp(e.target.value)} inputMode="numeric" placeholder="003551" />
        </Field>
        <Button type="submit" variant="primary" loading={run.isPending}>{!run.isPending && <Play className="size-4" aria-hidden="true" />}Chạy</Button>
        {r && <Button onClick={() => { run.reset(); onPath(new Set()); setChoices({}); }} aria-label="Xoá kết quả"><RotateCcw className="size-4" aria-hidden="true" /></Button>}
      </form>
      {run.isError && <p role="alert" className="mt-2 text-sm text-failed">{errorMessage(run.error)}</p>}
      {r && (
        <div className="mt-4" aria-live="polite">
          {r.status === "outcome" && r.outcome && (
            <p className="mb-3 flex flex-wrap items-center gap-2">
              <StatusBadge status={OUTCOME[r.outcome.result].status} label={`Kết luận: ${r.outcome.label}`} />
              {r.outcome.approval && <span className="text-sm">{r.outcome.approval}</span>}
            </p>
          )}
          {r.status === "missing_data" && (
            <p className="mb-3 flex items-start gap-1.5 text-sm text-waiting"><CircleAlert className="mt-0.5 size-4 shrink-0" aria-hidden="true" />Chưa đủ dữ liệu: {last?.missing ?? "không rõ"} của Mã NV {r.employee_id}. Đồng bộ lại hoặc xử lý thủ công theo hướng dẫn.</p>
          )}
          {r.status === "end" && <p className="mb-3 text-sm text-muted">Đã đi hết nhánh, không có bước kết luận.</p>}
          <ol className="space-y-2 border-l border-line pl-4 text-sm">
            {r.trace.map((t) => (
              <li key={t.id}>
                <button type="button" onClick={() => onSelect(t.id)} className="text-left font-medium hover:text-primary">{t.title}</button>
                {t.kind === "check" && t.left !== undefined && (
                  <span className="mt-0.5 block text-muted">
                    {t.left ? <>{t.left.label} = <b className="text-ink">{show(t.left)}</b></> : <i>{t.missing}: chưa có dữ liệu</i>} {OP_LABEL[t.op!]}{" "}
                    {t.right ? <>{t.right.label} = <b className="text-ink">{show(t.right)}</b></> : <i>{t.missing}: chưa có dữ liệu</i>}
                    {t.result !== undefined && <> → <b className={t.result ? "text-done" : "text-failed"}>{t.result ? "Đúng" : "Sai"}</b></>}
                    {[t.left, t.right].filter((v) => v && v.source !== "Nhập trong Playbook").map((v) => <span key={v!.label} className="block text-xs">Nguồn: {v!.source}</span>)}
                  </span>
                )}
                {t.chosen !== undefined && <span className="block text-muted">Đã chọn: {tree.nodes[t.id]?.children[t.chosen]?.label || tree.nodes[tree.nodes[t.id]?.children[t.chosen]?.to ?? ""]?.title}</span>}
              </li>
            ))}
          </ol>
          {r.status === "needs_choice" && last?.options && (
            <div className="mt-3">
              <p className="mb-1.5 text-sm font-medium">Chọn nhánh để chạy tiếp:</p>
              <div className="flex flex-wrap gap-2">
                {last.options.map((o) => (
                  <Button key={o.index} size="sm" onClick={() => go({ ...choices, [last.id]: o.index })}>
                    <CornerDownRight className="size-4" aria-hidden="true" />{o.label || tree.nodes[o.to]?.title}
                  </Button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
