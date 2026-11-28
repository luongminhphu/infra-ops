import { formatNumber } from "@/core/lib/format";

/** Shown when the API returned fewer rows than match, so a capped list is never mistaken for the whole set. */
export function TruncationNotice({ shown, total }: { shown: number; total?: number }) {
  if (total == null || total <= shown) return null;
  return (
    <p role="status" className="mt-2 text-sm text-muted">
      Đang hiện <span className="tabular">{formatNumber(shown)}</span> / <span className="tabular">{formatNumber(total)}</span> – thu hẹp bằng bộ lọc hoặc tìm kiếm để thấy phần còn lại.
    </p>
  );
}
