import { useRef, useState } from "react";
import { Download, FileSpreadsheet, ScanSearch } from "lucide-react";
import { toast } from "sonner";
import { api, downloadFile, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { DateInput } from "@/core/ui/date-input";
import { Mono, PageHeader, Panel } from "@/core/ui/feedback";
import { Field, Textarea } from "@/core/ui/field";
import { HelpTip } from "@/core/ui/help-tip";

const MAX_DAYS = 62;
const MAX_IDS = 500;

/** IDs typed or left in the box (words such as names are not counted; the server recognises tables). */
export function parseIds(text: string): string[] {
  return [...new Set(text.split(/[\s,;]+/).filter((t) => /^[0-9A-Za-z]*\d[0-9A-Za-z]*$/.test(t)))];
}

function firstOfMonth(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-01`;
}

function today(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

interface Recognized {
  items: { employee_id: string; name: string }[];
  total: number;
  skipped: number;
  column: string | null;
  max: number;
}

/** Text that is more than a list of IDs (a table copied from an e-mail or Excel, IDs mixed with names). */
export function looksLikeTable(text: string): boolean {
  return text.includes("\t") || /\p{L}/u.test(text);
}

export function AttendancePage() {
  const [ids, setIds] = useState("");
  const [found, setFound] = useState<Recognized | null>(null);
  const [reading, setReading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);
  const [from, setFrom] = useState(firstOfMonth());
  const [to, setTo] = useState(today());
  const [touched, setTouched] = useState(false);
  const [busy, setBusy] = useState(false);

  const list = parseIds(ids);
  const days = from && to ? (Date.parse(to) - Date.parse(from)) / 86_400_000 + 1 : 0;
  const idError = list.length === 0 ? "Nhập ít nhất một Mã NV." : list.length > MAX_IDS ? `Tối đa ${MAX_IDS} Mã NV mỗi lần.` : "";
  const dateError =
    !from || !to ? "Nhập đủ ngày bắt đầu và kết thúc (dd/mm/yyyy)." : days < 1 ? "Ngày kết thúc phải sau ngày bắt đầu." : days > MAX_DAYS ? `Tối đa ${MAX_DAYS} ngày mỗi lần xuất.` : "";

  // HR's list (file or pasted table) → the server recognises the ID column; the box then holds one ID per line.
  const recognize = async (body: FormData, label: string) => {
    setReading(true);
    try {
      const r = await api<Recognized>("/api/lifecycle/attendance/parse", { method: "POST", body });
      setFound(r);
      setIds(r.items.map((i) => i.employee_id).join("\n"));
      toast.success(`Đã nhận diện ${r.total} Mã NV từ ${label}${r.skipped ? ` (bỏ qua ${r.skipped} dòng không có mã)` : ""}.`);
    } catch (e) {
      toast.error(errorMessage(e));
    } finally {
      setReading(false);
    }
  };
  const recognizeText = (text: string) => {
    const fd = new FormData();
    fd.set("text", text);
    void recognize(fd, "bảng đã dán");
  };

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
            hint={`Dán nguyên bảng từ email/Excel của HR (app tự tìm cột Mã NV) hoặc gõ danh sách mã. Đang có ${list.length} Mã NV.`}
            error={idError}
            showError={touched}
            required
          >
            <Textarea
              rows={6}
              value={ids}
              onChange={(e) => { setIds(e.target.value); setFound(null); }}
              onPaste={(e) => {
                const text = e.clipboardData.getData("text/plain");
                if (!looksLikeTable(text)) return;
                e.preventDefault();
                recognizeText(text);
              }}
              placeholder="Dán bảng từ email của HR, hoặc ví dụ: 003551, 003475"
              className="font-mono"
            />
          </Field>
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileRef}
              type="file"
              accept=".xlsx,.xlsm,.csv,.txt"
              className="sr-only"
              aria-label="Chọn file danh sách nhân viên"
              onChange={(e) => {
                const f = e.target.files?.[0];
                e.target.value = "";
                if (!f) return;
                const fd = new FormData();
                fd.set("file", f);
                void recognize(fd, `file ${f.name}`);
              }}
            />
            <Button onClick={() => fileRef.current?.click()} loading={reading}>
              {!reading && <FileSpreadsheet className="size-4" aria-hidden="true" />}Nhập file Excel / CSV
            </Button>
            {ids.trim() && looksLikeTable(ids) && (
              <Button onClick={() => recognizeText(ids)} loading={reading}>
                {!reading && <ScanSearch className="size-4" aria-hidden="true" />}Nhận diện Mã NV
              </Button>
            )}
          </div>
          {found && (
            <details className="rounded-md border border-line bg-surface-2 px-3 py-2 text-sm">
              <summary className="cursor-pointer">
                <b className="font-semibold">Đã nhận diện {found.total} nhân viên</b>
                {found.column ? ` từ cột “${found.column}”` : " (mã 4–8 chữ số trong từng dòng)"}
                {found.skipped > 0 && ` · bỏ qua ${found.skipped} dòng không có mã`}
                {found.total > found.max && ` · chỉ lấy ${found.max} người đầu`}
              </summary>
              <ul className="mt-2 max-h-56 columns-1 gap-6 overflow-y-auto sm:columns-2" tabIndex={0} aria-label="Nhân viên đã nhận diện">
                {found.items.map((i) => (
                  <li key={i.employee_id} className="break-inside-avoid py-0.5"><Mono>{i.employee_id}</Mono>{i.name && <span className="text-muted"> · {i.name}</span>}</li>
                ))}
              </ul>
            </details>
          )}
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
