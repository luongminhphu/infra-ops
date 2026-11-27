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
  hr_status: string; // what HR reads in the e-mail and Excel
  closed_on: string | null; // the day IT completed the offboard Job
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
        description="Hằng tháng IT gửi phòng Nhân sự danh sách người nghỉ việc theo request ITSM và tình trạng thu hồi, để HR đối chiếu với hệ thống nhân sự và xác nhận danh sách đầy đủ, đúng (ISO 27001)."
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
              <span className="block">Danh sách: request ITSM (SDP) loại nghỉ việc có ngày hiệu lực trong tháng. Trạng thái xử lý: “Đã thu hồi” khi Job offboard hoàn tất, không còn tài sản và ra vào BioStar đã khoá (theo lần đồng bộ gần nhất).</span>
              <span className="block">Email và file Excel gửi HR chỉ có Mã NV, họ tên, phòng ban, ngày nghỉ, request, trạng thái và ngày IT hoàn tất – chi tiết kỹ thuật chỉ hiện ở trang này. File có cột “HR xác nhận” để HR điền và gửi lại; lưu email đã gửi làm bằng chứng rà soát.</span>
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
            {/* Phones: one card per person, the status and its action last. */}
            <ul className="space-y-2 sm:hidden" aria-label={`Nhân sự nghỉ việc tháng ${month}`}>
              {d.items.map((r) => (
                <li key={`${r.employee_id}-${r.request}`} className="rounded-md border border-line bg-surface p-3">
                  <p className="font-medium">{r.full_name} <Mono className="text-sm text-muted">{r.employee_id}</Mono></p>
                  <p className="mt-0.5 mb-2 text-sm text-muted">{r.department || "—"} · nghỉ {formatDate(r.effective_date)} · {r.request} {r.request_status}{r.done && r.closed_on ? ` · IT xong ${formatDate(r.closed_on)}` : ""}</p>
                  <Progress r={r} />
                </li>
              ))}
            </ul>
            <div className="relative hidden overflow-x-auto rounded-md border border-line bg-surface sm:block">
              <table className="w-full text-left">
                <caption className="sr-only">Nhân sự nghỉ việc tháng {month}</caption>
                <thead className="bg-surface-2 text-sm text-muted">
                  <tr>
                    {["Nhân viên", "Ngày nghỉ", "Request ITSM", "Trạng thái xử lý", "Ngày IT hoàn tất"].map((h) => (
                      <th key={h} scope="col" className="whitespace-nowrap px-3 py-2 font-semibold">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {d.items.map((r) => (
                    <tr key={`${r.employee_id}-${r.request}`} className="border-t border-line align-top">
                      <td className="px-3 py-2">
                        <span className="block font-medium">{r.full_name}</span>
                        <span className="block text-sm text-muted"><Mono>{r.employee_id}</Mono> · {r.department || "—"}</span>
                      </td>
                      <td className="tabular whitespace-nowrap px-3 py-2">{formatDate(r.effective_date)}</td>
                      <td className="whitespace-nowrap px-3 py-2"><Mono>{r.request}</Mono>{r.request_status && <span className="block text-xs text-muted">{r.request_status}</span>}</td>
                      <td className="px-3 py-2"><Progress r={r} /></td>
                      <td className="tabular whitespace-nowrap px-3 py-2">{r.done && r.closed_on ? formatDate(r.closed_on) : <span className="text-muted">—</span>}</td>
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

/**
 * Trạng thái xử lý: the HR-facing status, and for IT what is still open (Job, devices, door access) with the link
 * that finishes it. Only the status goes to HR.
 */
function Progress({ r }: { r: ReviewItem }) {
  if (r.done) return <StatusBadge status="done" label="Đã thu hồi" />;
  const open: string[] = [];
  if (r.job_status !== "completed") open.push(r.job_status ? `Job offboard: ${JOB_STATUS[r.job_status].label}` : "Chưa có Job offboard");
  if (r.assets_in_use === null) open.push("Tài sản: chưa đối soát");
  else if (r.assets_in_use) open.push(`Còn ${r.assets_in_use} tài sản`);
  if (r.biostar_active === null) open.push("BioStar: chưa đọc");
  else if (r.biostar_active) open.push("Ra vào BioStar còn hoạt động");
  return (
    <div className="grid justify-items-start gap-1">
      <StatusBadge status="warning" label="Đang xử lý" />
      <span className="text-sm text-muted text-pretty">{open.join(" · ")}</span>
      <RowAction r={r} />
    </div>
  );
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
