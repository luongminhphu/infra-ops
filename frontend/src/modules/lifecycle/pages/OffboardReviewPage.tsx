import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, ChevronRight, Download, Mail, UserCheck } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { EmptyState, ErrorState, LoadingRows, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Select } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";
import { StatusBadge } from "@/core/ui/status";
import { formatDate } from "@/core/lib/format";
import { JOB_STATUS, type JobStatus } from "../jobs";

interface ReviewItem {
  employee_id: string;
  full_name: string;
  department: string;
  effective_date: string;
  request: string;
  request_status: string;
  job_status: JobStatus | null;
  job_id: string | null;
  assets_in_use: number | null;
  biostar: string;
  biostar_active: boolean | null;
  done: boolean;
  conclusion: string;
}

interface Review {
  month: string;
  from: string;
  to: string;
  items: ReviewItem[];
  pending: number;
  hr_review_to: string[];
}

/** The last 12 months, newest first, as yyyy-MM. */
export function recentMonths(now: Date = new Date()): string[] {
  return Array.from({ length: 12 }, (_, i) => {
    const d = new Date(now.getFullYear(), now.getMonth() - i, 1);
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
  });
}

export function OffboardReviewPage() {
  const [params, setParams] = useSearchParams();
  const months = recentMonths();
  const month = params.get("month") ?? months[1];
  const [busy, setBusy] = useState<"" | "eml" | "xlsx">("");
  const q = useQuery({
    queryKey: ["lifecycle", "offboard-review", month],
    queryFn: () => api<Review>(`/api/lifecycle/offboard-review?month=${month}`),
  });

  const download = async (format: "eml" | "xlsx") => {
    setBusy(format);
    try {
      const res = await fetch(`/api/lifecycle/offboard-review/export?month=${month}&format=${format}`, { credentials: "same-origin" });
      if (!res.ok) throw new Error("Không xuất được file. Thử lại sau.");
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await res.blob());
      a.download = /filename="([^"]+)"/.exec(res.headers.get("Content-Disposition") ?? "")?.[1] ?? `ra-soat-nghi-viec.${format}`;
      a.click();
      URL.revokeObjectURL(a.href);
      if (format === "eml") toast.success("Đã tải email nháp. Mở bằng Outlook, kiểm tra rồi bấm Gửi.");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy("");
    }
  };

  const d = q.data;
  const ready = !!d && d.pending === 0 && d.hr_review_to.length > 0;
  return (
    <>
      <PageHeader
        title="Rà soát nghỉ việc (ISO 27001)"
        description="Hằng tháng IT gửi phòng Nhân sự danh sách người nghỉ việc theo request RTR, kèm tình trạng thu hồi quyền truy cập và tài sản, để HR xác nhận danh sách đầy đủ và đúng (rà soát quyền truy cập định kỳ theo ISO 27001)."
        actions={
          // The e-mail is the main action only when the list is ready to send (all cases done, recipient set).
          <span className="inline-flex flex-wrap gap-2">
            <Button variant={ready ? "secondary" : "primary"} onClick={() => download("xlsx")} loading={busy === "xlsx"} disabled={!d}>
              {busy !== "xlsx" && <Download className="size-4" aria-hidden="true" />}Xuất Excel
            </Button>
            <Button variant={ready ? "primary" : "secondary"} onClick={() => download("eml")} loading={busy === "eml"} disabled={!d}>
              {busy !== "eml" && <Mail className="size-4" aria-hidden="true" />}Tải email nháp gửi HR
            </Button>
          </span>
        }
      />
      {d && (d.pending > 0 || d.hr_review_to.length === 0) && (
        <div role="status" className="mb-4 flex gap-2 rounded-md border border-waiting bg-waiting-soft px-3 py-2 text-sm">
          <AlertTriangle className="mt-0.5 size-4 shrink-0 text-waiting" aria-hidden="true" />
          <span>
            {d.pending > 0 && <span className="block"><b className="font-semibold">Còn {d.pending} trường hợp IT chưa thu hồi xong.</b> Xử lý Job offboard của từng người (liên kết ở mỗi dòng) trước khi gửi HR, hoặc ghi rõ trong email là đang xử lý.</span>}
            {d.hr_review_to.length === 0 && <span className="block">Chưa có email HR nhận rà soát – điền ở <Link to="/lifecycle/settings" className="font-medium text-primary underline">Cấu hình module</Link>.</span>}
          </span>
        </div>
      )}
      <div className="mb-4 flex flex-wrap items-end gap-4">
        <Field label="Tháng nghỉ việc" className="w-44">
          <Select value={month} onChange={(e) => setParams({ month: e.target.value }, { replace: true })}>
            {months.map((m) => (
              <option key={m} value={m}>{`${m.slice(5)}/${m.slice(0, 4)}`}</option>
            ))}
          </Select>
        </Field>
        {d && (
          <p className="pb-2 text-sm text-muted">
            {d.items.length} người nghỉ việc ({formatDate(d.from)} – {formatDate(d.to)}){d.hr_review_to.length > 0 && <> · Email gửi đến: {d.hr_review_to.join(", ")}</>}
            <HelpTip title="Số liệu lấy từ đâu" className="ml-1">
              <span className="block">Danh sách: request RTR loại nghỉ việc có ngày hiệu lực trong tháng. Tài sản và BioStar: lần đồng bộ đối soát gần nhất; Job: Job offboard mới nhất của người đó.</span>
              <span className="block">Email nháp mở được bằng Outlook, đã kèm file Excel có cột “HR xác nhận” để HR điền và gửi lại. Lưu email đã gửi làm bằng chứng rà soát.</span>
            </HelpTip>
          </p>
        )}
      </div>
      {q.error && <ErrorState error={q.error} onRetry={() => q.refetch()} />}
      {q.isLoading ? (
        <LoadingRows rows={5} />
      ) : d && d.items.length === 0 ? (
        <EmptyState icon={UserCheck} title="Không có nhân sự nghỉ việc trong tháng">Vẫn có thể tải email nháp để HR xác nhận tháng này không có trường hợp nào.</EmptyState>
      ) : (
        d && (
          <>
            {/* Phones: one card per person, the decision (conclusion + action) first. */}
            <ul className="space-y-2 sm:hidden" aria-label={`Nhân sự nghỉ việc tháng ${month}`}>
              {d.items.map((r) => (
                <li key={`${r.employee_id}-${r.request}`} className="rounded-md border border-line bg-surface p-3">
                  <div className="flex items-start justify-between gap-2">
                    <p className="font-medium">{r.full_name} <Mono className="text-sm text-muted">{r.employee_id}</Mono></p>
                    <Conclusion r={r} />
                  </div>
                  <p className="mt-1 text-sm text-muted">{r.department || "—"} · nghỉ {formatDate(r.effective_date)} · {r.request} {r.request_status}</p>
                  <div className="mt-2 flex flex-wrap gap-1.5"><JobBadge r={r} /><AssetBadge r={r} /><BioStarBadge r={r} /></div>
                  <RowAction r={r} className="mt-2" />
                </li>
              ))}
            </ul>
            <div className="relative hidden overflow-x-auto rounded-md border border-line bg-surface sm:block">
              <table className="w-full text-left">
                <caption className="sr-only">Nhân sự nghỉ việc tháng {month}</caption>
                <thead className="bg-surface-2 text-sm text-muted">
                  <tr>
                    {["Nhân viên", "Ngày nghỉ", "Request RTR", "Job offboard", "Tài sản còn gán", "BioStar", "Kết luận IT"].map((h) => (
                      <th key={h} scope="col" className="whitespace-nowrap px-3 py-2 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {d.items.map((r) => (
                    <tr key={`${r.employee_id}-${r.request}`} className="border-t border-line align-top">
                      <td className="px-3 py-1.5">
                        <span className="block font-medium">{r.full_name}</span>
                        <span className="block text-sm text-muted"><Mono>{r.employee_id}</Mono> · {r.department || "—"}</span>
                      </td>
                      <td className="tabular whitespace-nowrap px-3 py-1.5">{formatDate(r.effective_date)}</td>
                      <td className="whitespace-nowrap px-3 py-1.5"><Mono>{r.request}</Mono>{r.request_status && <span className="block text-xs text-muted">{r.request_status}</span>}</td>
                      <td className="whitespace-nowrap px-3 py-1.5"><JobBadge r={r} /></td>
                      <td className="whitespace-nowrap px-3 py-1.5"><AssetBadge r={r} /></td>
                      <td className="whitespace-nowrap px-3 py-1.5"><BioStarBadge r={r} /></td>
                      <td className="px-3 py-1.5">
                        <Conclusion r={r} />
                        {!r.done && <span className="mt-0.5 block text-xs text-muted">{r.conclusion.replace("Cần xử lý: ", "")}</span>}
                        <RowAction r={r} className="mt-1" />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )
      )}
    </>
  );
}

function JobBadge({ r }: { r: ReviewItem }) {
  return r.job_status ? <StatusBadge {...JOB_STATUS[r.job_status]} /> : <StatusBadge status="pending" label="Chưa có Job" />;
}

function AssetBadge({ r }: { r: ReviewItem }) {
  if (r.assets_in_use === null) return <StatusBadge status="skipped" label="Chưa đối soát" />;
  return r.assets_in_use ? <StatusBadge status="warning" label={`Còn ${r.assets_in_use} tài sản`} /> : <StatusBadge status="done" label="Không còn tài sản" />;
}

function BioStarBadge({ r }: { r: ReviewItem }) {
  if (r.biostar_active === null) return <StatusBadge status="skipped" label="BioStar chưa đọc" />;
  return r.biostar_active ? <StatusBadge status="failed" label="BioStar còn hoạt động" /> : <StatusBadge status="done" label={`BioStar: ${r.biostar}`} />;
}

function Conclusion({ r }: { r: ReviewItem }) {
  return <StatusBadge status={r.done ? "done" : "warning"} label={r.done ? "Đã thu hồi đủ" : "Cần xử lý"} />;
}

/** Where IT finishes the case: the offboard job, or the reconciliation panel that creates it. */
function RowAction({ r, className }: { r: ReviewItem; className?: string }) {
  if (r.done) return null;
  const to = r.job_id ? `/lifecycle/jobs/${r.job_id}` : `/lifecycle/reconciliation?emp=${r.employee_id}`;
  return (
    <Link to={to} className={`inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline ${className ?? ""}`}>
      {r.job_id ? "Mở Job offboard" : "Tạo Job offboard"}<ChevronRight className="size-4" aria-hidden="true" />
    </Link>
  );
}
