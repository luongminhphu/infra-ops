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

/** yyyy-MM-dd in Vietnam time, `offsetDays` from `now`. */
export function isoDateVn(offsetDays = 0, now: Date = new Date()): string {
  return formatInTimeZone(new Date(now.getTime() + offsetDays * 86_400_000), TIME_ZONE, "yyyy-MM-dd");
}

/** 1.234.567 (vi-VN grouping). */
export function formatNumber(n: number): string {
  return new Intl.NumberFormat("vi-VN").format(n);
}

/** 1.234.567 đ */
export function formatVnd(value: number | null | undefined, fallback = "—"): string {
  return value == null ? fallback : `${formatNumber(value)} đ`;
}

/** Removes Vietnamese diacritics so "ket noi" finds "Kết nối". */
export function fold(s: string): string {
  return s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").replace(/đ/g, "d").replace(/Đ/g, "D").toLowerCase();
}
