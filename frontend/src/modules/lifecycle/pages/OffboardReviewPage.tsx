import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, useSearchParams } from "react-router-dom";
import { Download, Mail, UserCheck } from "lucide-react";
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
  return (
    <>
      <PageHeader
        title="Rà soát nhân sự nghỉ việc"
        description="Hằng tháng IT gửi phòng Nhân sự danh sách người nghỉ việc theo request RTR, kèm tình trạng thu hồi quyền truy cập và tài sản, để HR xác nhận danh sách đầy đủ và đúng (rà soát quyền truy cập định kỳ theo ISO 27001)."
        actions={
          <span className="inline-flex flex-wrap gap-2">
            <Button onClick={() => download("xlsx")} loading={busy === "xlsx"} disabled={!d}>
              {busy !== "xlsx" && <Download className="size-4" aria-hidden="true" />}Xuất Excel
            </Button>
            <Button variant="primary" onClick={() => download("eml")} loading={busy === "eml"} disabled={!d}>
              {busy !== "eml" && <Mail className="size-4" aria-hidden="true" />}Tải email nháp gửi HR
            </Button>
          </span>
        }
      />
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
            {d.items.length} người nghỉ việc từ {formatDate(d.from)} đến {formatDate(d.to)} · {d.pending ? <span className="text-warning">{d.pending} trường hợp IT còn phải xử lý</span> : "IT đã thu hồi đủ"} · Email gửi đến: {d.hr_review_to.length ? d.hr_review_to.join(", ") : <Link to="/lifecycle/settings" className="text-primary underline">chưa cấu hình</Link>}
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
          <div className="relative overflow-x-auto rounded-md border border-line bg-surface">
            <table className="w-full text-left">
              <caption className="sr-only">Nhân sự nghỉ việc tháng {month}</caption>
              <thead className="bg-surface-2 text-sm text-muted">
                <tr>
                  {["Mã NV", "Họ tên", "Phòng ban", "Ngày nghỉ", "Request RTR", "Job offboard", "Tài sản còn gán", "BioStar", "Kết luận IT"].map((h) => (
                    <th key={h} scope="col" className="whitespace-nowrap px-3 py-2 font-semibold">{h}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {d.items.map((r) => (
                  <tr key={`${r.employee_id}-${r.request}`} className="border-t border-line">
                    <td className="px-3 py-1.5"><Link to={`/lifecycle/reconciliation?emp=${r.employee_id}`} className="text-primary underline-offset-2 hover:underline"><Mono>{r.employee_id}</Mono></Link></td>
                    <td className="px-3 py-1.5">{r.full_name}</td>
                    <td className="px-3 py-1.5 text-muted">{r.department || "—"}</td>
                    <td className="tabular whitespace-nowrap px-3 py-1.5">{formatDate(r.effective_date)}</td>
                    <td className="whitespace-nowrap px-3 py-1.5"><Mono>{r.request}</Mono>{r.request_status && <span className="block text-xs text-muted">{r.request_status}</span>}</td>
                    <td className="whitespace-nowrap px-3 py-1.5">{r.job_status ? <StatusBadge {...JOB_STATUS[r.job_status]} /> : <StatusBadge status="pending" label="Chưa có Job" />}</td>
                    <td className="tabular px-3 py-1.5">{r.assets_in_use ?? "Chưa đọc"}</td>
                    <td className="whitespace-nowrap px-3 py-1.5">{r.biostar}</td>
                    <td className="px-3 py-1.5"><StatusBadge status={r.done ? "done" : "warning"} label={r.done ? "Đã thu hồi đủ" : "Cần xử lý"} />{!r.done && <span className="mt-0.5 block text-xs text-muted">{r.conclusion.replace("Cần xử lý: ", "")}</span>}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      )}
    </>
  );
}
