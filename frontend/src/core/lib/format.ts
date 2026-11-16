import { formatInTimeZone } from "date-fns-tz";

export const TIME_ZONE = "Asia/Ho_Chi_Minh";

function toDate(value: string | Date | null | undefined): Date | null {
  if (!value) return null;
  const d = typeof value === "string" ? new Date(value) : value;
  return Number.isNaN(d.getTime()) ? null : d;
}

/** dd/MM/yyyy in Vietnam time. */
export function formatDate(value: string | Date | null | undefined, fallback = "—"): string {
  const d = toDate(value);
  return d ? formatInTimeZone(d, TIME_ZONE, "dd/MM/yyyy") : fallback;
}

/** dd/MM/yyyy HH:mm in Vietnam time. */
export function formatDateTime(value: string | Date | null | undefined, fallback = "—"): string {
  const d = toDate(value);
  return d ? formatInTimeZone(d, TIME_ZONE, "dd/MM/yyyy HH:mm") : fallback;
}

/** Relative wording for short spans, absolute date otherwise. */
export function formatRelative(value: string | Date | null | undefined, now: Date = new Date()): string {
  const d = toDate(value);
  if (!d) return "Chưa có";
  const minutes = Math.round((now.getTime() - d.getTime()) / 60000);
  if (minutes < 1) return "Vừa xong";
  if (minutes < 60) return `${minutes} phút trước`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} giờ trước`;
  return formatDateTime(d);
}

export function pluralDays(days: number): string {
  if (days < 0) return `quá hạn ${Math.abs(days)} ngày`;
  if (days === 0) return "hết hạn hôm nay";
  return `còn ${days} ngày`;
}
