import { formatInTimeZone } from "date-fns-tz";
import { TIME_ZONE } from "@/core/lib/format";

export type PresetKey = "week" | "month" | "quarter" | "half" | "year" | "custom";

export const PRESETS: { key: PresetKey; label: string }[] = [
  { key: "week", label: "Tuần này" },
  { key: "month", label: "Tháng này" },
  { key: "quarter", label: "Quý này" },
  { key: "half", label: "6 tháng" },
  { key: "year", label: "1 năm" },
  { key: "custom", label: "Theo lịch" },
];

export function todayVn(now: Date = new Date()): string {
  return formatInTimeZone(now, TIME_ZONE, "yyyy-MM-dd");
}

const iso = (d: Date) => d.toISOString().slice(0, 10);

/** Inclusive [from, to] ISO dates for a preset, ending today (Vietnam time). */
export function presetRange(key: Exclude<PresetKey, "custom">, today: string): { from: string; to: string } {
  const t = new Date(`${today}T00:00:00Z`);
  const y = t.getUTCFullYear();
  const m = t.getUTCMonth();
  let from: Date;
  if (key === "week") {
    from = new Date(t);
    from.setUTCDate(t.getUTCDate() - ((t.getUTCDay() + 6) % 7)); // Monday
  } else if (key === "month") from = new Date(Date.UTC(y, m, 1));
  else if (key === "quarter") from = new Date(Date.UTC(y, m - (m % 3), 1));
  else if (key === "half") from = new Date(Date.UTC(y, m - 5, 1));
  else from = new Date(Date.UTC(y, m - 11, 1));
  return { from: iso(from), to: today };
}

/** Axis label for a series bucket start date. */
export function bucketLabel(period: string, bucket: "day" | "week" | "month"): string {
  const [yy, mm, dd] = period.split("-");
  if (bucket === "month") return `${mm}/${yy}`;
  return `${dd}/${mm}`;
}
