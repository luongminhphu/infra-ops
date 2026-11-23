import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ChartColumn } from "lucide-react";
import { api } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { DateInput } from "@/core/ui/date-input";
import { EmptyState, ErrorState, LoadingRows, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Select } from "@/core/ui/field";
import { formatDate } from "@/core/lib/format";
import { useTechnicians, type Stats } from "../api";
import { PRESETS, bucketLabel, presetRange, todayVn, type PresetKey } from "../periods";

const BUCKET_LABEL = { day: "ngày", week: "tuần", month: "tháng" } as const;
const pct = (v: number | null) => (v == null ? "—" : `${Math.round(v * 100)}%`);
const num = (v: number) => v.toLocaleString("vi-VN", { maximumFractionDigits: 1 });
/** Average handling time: hours below two days, days above. */
const duration = (h: number | null) => (h == null ? "—" : h < 48 ? `${num(h)} giờ` : `${num(h / 24)} ngày`);

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <div className="rounded-md border border-line bg-surface px-4 py-3">
      <p className="text-sm text-muted">{label}</p>
      <p className="mt-0.5 text-2xl font-semibold tabular">{value}</p>
      {hint && <p className="text-xs text-muted">{hint}</p>}
    </div>
  );
}

const SERIES = [
  { key: "received", label: "Tiếp nhận", color: "var(--series-1)" },
  { key: "completed", label: "Hoàn tất", color: "var(--series-2)" },
] as const;

/** Grouped columns: received vs completed per period. Hover/focus a period for exact values. */
function Chart({ data }: { data: Stats }) {
  const max = Math.max(1, ...data.series.flatMap((p) => [p.received, p.completed]));
  const ticks = [max, max / 2, 0].map(num);
  return (
    <figure>
      <figcaption className="mb-2 flex flex-wrap items-center gap-4 text-sm">
        <span className="font-medium">Tiếp nhận và hoàn tất theo {BUCKET_LABEL[data.bucket]}</span>
        <span className="flex gap-3" aria-hidden="true">
          {SERIES.map((s) => (
            <span key={s.key} className="inline-flex items-center gap-1.5 text-muted">
              <span className="size-2.5 rounded-xs" style={{ background: s.color }} />
              {s.label}
            </span>
          ))}
        </span>
      </figcaption>
      <div className="flex gap-2">
        <div className="flex h-48 flex-col justify-between pb-5 text-right text-xs text-muted tabular" aria-hidden="true">
          {ticks.map((t, i) => <span key={i}>{t}</span>)}
        </div>
        <div className="relative min-w-0 flex-1 overflow-x-auto">
          <div className="pointer-events-none absolute inset-x-0 top-0 h-43 border-t border-b border-line" aria-hidden="true">
            <div className="absolute inset-x-0 top-1/2 border-t border-dashed border-line" />
          </div>
          <ul className="relative flex h-48 items-end" aria-label="Số task theo kỳ">
            {data.series.map((p) => (
              <li
                key={p.period}
                tabIndex={0}
                aria-label={`${bucketLabel(p.period, data.bucket)}: ${p.received} tiếp nhận, ${p.completed} hoàn tất`}
                className="group relative flex h-full min-w-7 flex-1 flex-col items-center justify-end outline-offset-[-2px] hover:bg-surface-2 focus-visible:bg-surface-2"
              >
                <div className="flex h-43 items-end gap-0.5" aria-hidden="true">
                  {SERIES.map((s) => (
                    <span
                      key={s.key}
                      className="w-2.5 rounded-t-[4px]"
                      style={{ height: `${(p[s.key] / max) * 100}%`, background: s.color, minHeight: p[s.key] ? 2 : 0 }}
                    />
                  ))}
                </div>
                <span className="h-5 pt-0.5 text-[11px] whitespace-nowrap text-muted tabular" aria-hidden="true">
                  {bucketLabel(p.period, data.bucket)}
                </span>
                <span
                  role="presentation"
                  className="pointer-events-none absolute bottom-full z-10 mb-1 hidden rounded-sm border border-line bg-surface px-2 py-1 text-xs whitespace-nowrap shadow-md group-hover:block group-focus-visible:block"
                >
                  <strong className="block">{bucketLabel(p.period, data.bucket)}</strong>
                  {SERIES.map((s) => (
                    <span key={s.key} className="flex items-center gap-1.5">
                      <span className="size-2 rounded-xs" style={{ background: s.color }} />
                      {s.label}: <span className="font-semibold tabular">{p[s.key]}</span>
                    </span>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        </div>
      </div>
      <details className="mt-3 text-sm">
        <summary className="cursor-pointer font-medium">Xem dạng bảng</summary>
        <table className="mt-2 w-full max-w-md text-left">
          <thead><tr className="text-muted"><th className="py-1 font-medium">Kỳ</th><th className="py-1 text-right font-medium">Tiếp nhận</th><th className="py-1 text-right font-medium">Hoàn tất</th></tr></thead>
          <tbody>
            {data.series.map((p) => (
              <tr key={p.period} className="border-t border-line"><td className="py-1 tabular">{formatDate(p.period)}</td><td className="py-1 text-right tabular">{p.received}</td><td className="py-1 text-right tabular">{p.completed}</td></tr>
            ))}
          </tbody>
        </table>
      </details>
    </figure>
  );
}

export function StatsPage() {
  const today = todayVn();
  const [preset, setPreset] = useState<PresetKey>("month");
  const [range, setRange] = useState(presetRange("month", today));
  const [custom, setCustom] = useState(range);
  const [tech, setTech] = useState("");
  const techs = useTechnicians();
  const customValid = Boolean(custom.from && custom.to && custom.from <= custom.to);
  const stats = useQuery({
    queryKey: ["workload", "stats", range.from, range.to, tech],
    queryFn: () => api<Stats>(`/api/workload/stats?${new URLSearchParams({ from: range.from, to: range.to, technician_id: tech })}`),
  });
  const d = stats.data;
  const choose = (k: PresetKey) => {
    setPreset(k);
    if (k !== "custom") setRange(presetRange(k, today));
  };
  return (
    <>
      <PageHeader title="Thống kê công việc" description="Task Helpdesk/Support tiếp nhận, hoàn tất và quá hạn theo Operator. Ngày tính theo giờ Việt Nam." />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div role="group" aria-label="Kỳ thống kê" className="flex flex-wrap gap-1.5">
          {PRESETS.map((p) => (
            <Button key={p.key} size="sm" variant={preset === p.key ? "primary" : "secondary"} aria-pressed={preset === p.key} onClick={() => choose(p.key)}>
              {p.label}
            </Button>
          ))}
        </div>
        {preset === "custom" && (
          <form
            className="flex flex-wrap items-end gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              if (customValid) setRange(custom);
            }}
          >
            <Field label="Từ ngày"><DateInput value={custom.from} onChange={(v) => setCustom((c) => ({ ...c, from: v }))} /></Field>
            <Field label="Đến ngày" error="Ngày kết thúc phải sau ngày bắt đầu." showError={Boolean(custom.from && custom.to) && !customValid}>
              <DateInput value={custom.to} onChange={(v) => setCustom((c) => ({ ...c, to: v }))} />
            </Field>
            <Button type="submit" size="sm" variant="primary" disabled={!customValid} className="mb-1">Xem</Button>
          </form>
        )}
        <Field label="Operator" className="ml-auto w-60">
          <Select value={tech} onChange={(e) => setTech(e.target.value)}>
            <option value="">Tất cả</option>
            {techs.data?.items.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </Select>
        </Field>
      </div>
      <p className="mb-3 text-sm text-muted">Kỳ: {formatDate(range.from)} – {formatDate(range.to)}</p>

      {stats.isError && <ErrorState error={stats.error} onRetry={() => stats.refetch()} />}
      {stats.isLoading && <LoadingRows rows={6} />}
      {d && (
        <>
          <div className="mb-4 grid gap-3 sm:grid-cols-3 xl:grid-cols-6">
            <Tile label="Tiếp nhận trong kỳ" value={d.totals.received} />
            <Tile label="Hoàn tất trong kỳ" value={d.totals.completed} />
            <Tile label="Hoàn tất đúng hạn" value={pct(d.totals.on_time_rate)} hint={`${d.totals.completed_late} task trễ hạn`} />
            <Tile label="Thời gian xử lý TB" value={duration(d.totals.avg_hours)} />
            <Tile label="Đang chờ thực hiện" value={d.totals.open} hint="Tại thời điểm hiện tại" />
            <Tile label="Đang quá hạn" value={d.totals.overdue} hint="Tại thời điểm hiện tại" />
          </div>
          {d.series.length ? (
            <Panel className="mb-4"><Chart data={d} /></Panel>
          ) : (
            <EmptyState icon={ChartColumn} title="Không có task trong kỳ này">Chọn kỳ khác hoặc kiểm tra lần đồng bộ gần nhất.</EmptyState>
          )}
          <Panel title="Theo Operator" bodyClassName="p-0">
            <div className="overflow-x-auto">
              <table className="w-full text-left text-sm">
                <caption className="sr-only">Thống kê theo Operator</caption>
                <thead className="bg-surface-2 text-muted">
                  <tr>
                    {["Operator", "Tiếp nhận", "Hoàn tất", "Đúng hạn", "Trễ hạn", "Tỷ lệ đúng hạn", "TB xử lý", "Đang chờ", "Quá hạn"].map((h, i) => (
                      <th key={h} scope="col" className={`px-3 py-2 font-semibold whitespace-nowrap ${i ? "text-right" : ""}`}>{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {d.technicians.map((r) => (
                    <tr key={r.technician_id ?? "none"} className="border-t border-line">
                      <th scope="row" className="px-3 py-2 font-medium">{r.name ?? <span className="text-muted">Chưa giao</span>}</th>
                      {[r.received, r.completed, r.completed_on_time, r.completed_late, pct(r.on_time_rate), duration(r.avg_hours), r.open, r.overdue].map((v, i) => (
                        <td key={i} className={`px-3 py-2 text-right tabular ${i === 7 && r.overdue ? "font-semibold text-failed" : ""}`}>{v}</td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Panel>
        </>
      )}
    </>
  );
}
