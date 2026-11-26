import { useState } from "react";
import { Download } from "lucide-react";
import { toast } from "sonner";
import { downloadFile, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { DateInput } from "@/core/ui/date-input";
import { PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";

const MAX_DAYS = 62;
const MAX_IDS = 500;

export function parseIds(text: string): string[] {
  return [...new Set(text.split(/[\s,;]+/).filter(Boolean))];
}

function firstOfMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

export function AttendancePage() {
  const [ids, setIds] = useState("");
  const [from, setFrom] = useState(firstOfMonth());
  const [to, setTo] = useState(today());
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);

  const list = parseIds(ids);
  const days = from && to ? (Date.parse(to) - Date.parse(from)) / 86_400_000 + 1 : 0;
  const idError = list.length === 0 ? "Nhập ít nhất một Mã NV." : list.length > MAX_IDS ? `Tối đa ${MAX_IDS} Mã NV mỗi lần.` : "";
  const dateError =
    !from || !to ? "Nhập đủ ngày bắt đầu và kết thúc (dd/mm/yyyy)." : days < 1 ? "Ngày kết thúc phải sau ngày bắt đầu." : days > MAX_DAYS ? `Tối đa ${MAX_DAYS} ngày mỗi lần xuất.` : "";

  const run = async () => {
    setTouched(true);
    if (idError || dateError) return;
    setBusy(true);
    try {
      const h = await downloadFile("/api/lifecycle/attendance/export", { employee_ids: ids, date_from: from, date_to: to }, "cham-cong.xlsx");
      const missing = Number(h.get("X-Not-Found") ?? 0);
      const errors = Number(h.get("X-Read-Errors") ?? 0);
      if (errors) toast.warning("Có hệ thống BioStar không đọc được – xem sheet Ghi chú trong file.");
      else if (missing) toast.warning(`Đã xuất file. ${missing} Mã NV không có trên BioStar – xem sheet Ghi chú.`);
      else toast.success("Đã xuất báo cáo chấm công.");
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <PageHeader
        title="Báo cáo chấm công"
        description="Khi HR gửi yêu cầu rà soát chấm công hoặc Overtime: nhập Mã NV và khoảng thời gian, infra-ops đọc log quẹt thẻ / vân tay / khuôn mặt trên mọi hệ thống BioStar và xuất file Excel."
      />
      <Panel
        title={
          <span className="inline-flex items-center gap-1.5">
            Yêu cầu rà soát
            <HelpTip title="Cách tính">
              <span className="block">Sheet “Tổng hợp theo ngày”: mỗi nhân viên một dòng mỗi ngày, giờ vào là lần quẹt thành công đầu tiên, giờ ra là lần cuối cùng trong ngày (giờ Việt Nam). Ngày không có lượt quẹt vẫn được liệt kê.</span>
              <span className="block">Sheet “Log chi tiết”: từng lượt quẹt thành công kèm thiết bị. Ca làm qua đêm chưa được gộp.</span>
            </HelpTip>
          </span>
        }
      >
        <form
          className="grid max-w-2xl gap-4"
          onSubmit={(e) => {
            e.preventDefault();
            void run();
          }}
        >
          <Field
            label="Mã nhân viên"
            hint={`Dán danh sách từ email hoặc Excel của HR, cách nhau bằng xuống dòng, dấu phẩy hoặc khoảng trắng. Đã nhận ${list.length} Mã NV.`}
            error={idError}
            showError={touched}
            required
          >
            <Textarea rows={6} value={ids} onChange={(e) => setIds(e.target.value)} placeholder={"003551\n003475\n001163"} className="font-mono" />
          </Field>
          <div className="flex flex-wrap items-start gap-4">
            <Field label="Từ ngày" required error={dateError} showError={touched}>
              <DateInput value={from} onChange={setFrom} />
            </Field>
            <Field label="Đến ngày" required>
              <DateInput value={to} onChange={setTo} />
            </Field>
          </div>
          <div>
            <Button type="submit" variant="primary" loading={busy}>
              {!busy && <Download className="size-4" aria-hidden="true" />}
              {busy ? "Đang đọc BioStar…" : "Xuất Excel"}
            </Button>
          </div>
        </form>
      </Panel>
    </>
  );
}
