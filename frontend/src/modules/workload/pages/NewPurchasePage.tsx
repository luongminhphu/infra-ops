import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, ChevronLeft, Paperclip, Send, X } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { EmptyState, Mono, PageHeader } from "@/core/ui/feedback";
import { Field, Input, Select, Textarea } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";
import { From, Wizard } from "@/core/ui/wizard";
import { useCatalog, type DeviceNeed, type Purchase } from "../api";
import { EVIDENCE_ACCEPT, EvidenceHelp, pastedImage, uploadEvidence } from "../evidence";
import { KIND_LABEL } from "../labels";
import { bestMatch } from "../text";

const STEPS = [
  { key: "device", title: "Thiết bị" },
  { key: "user", title: "Người dùng & lý do" },
  { key: "evidence", title: "Bằng chứng duyệt" },
  { key: "review", title: "Xem lại & gửi" },
];

/**
 * Device purchase proposal, step by step. Opened from a suggestion (helpdesk request: ?ticket=…, onboard
 * allocation: ?source=onboard&ref=…) everything known is pre-filled; the operator confirms, attaches the
 * Function Head approval and sends. No prices: the supervisor prices it when approving.
 */
export function NewPurchasePage() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const catalog = useCatalog();
  const ticketId = params.get("ticket");
  const source = ticketId ? "ticket" : params.get("source") === "onboard" ? "onboard" : "manual";
  const ref = params.get("ref") ?? "";
  const fromLabel = ticketId ? `từ request #${ref || ticketId}` : source === "onboard" ? `từ onboard #${ref}` : "";
  const qty = Number(params.get("qty"));
  const active = (catalog.data ?? []).filter((c) => c.active);
  const catalogName = (params.get("catalog") ?? "").toLowerCase();
  // A helpdesk request gives its subject, not a device: show it as context and try the price list.
  const subject = ticketId ? (params.get("item") ?? "") : "";
  const back = params.get("back")?.startsWith("/") ? params.get("back")! : null;

  const [i, setI] = useState(0);
  const [catalogId, setCatalogId] = useState<string | null>(null);
  const [item, setItem] = useState(ticketId ? "" : (params.get("item") ?? ""));
  const [kind, setKind] = useState<DeviceNeed | "new">((params.get("kind") as DeviceNeed | "new" | null) ?? (source === "onboard" ? "new" : "replace"));
  const [quantity, setQuantity] = useState(qty > 0 ? qty : 1);
  const [employee, setEmployee] = useState(params.get("employee") ?? "");
  const [reason, setReason] = useState(params.get("reason") ?? "");
  const [files, setFiles] = useState<File[]>([]);
  const [skipEvidence, setSkipEvidence] = useState(false);
  const [sent, setSent] = useState<Purchase | null>(null);

  const guess = active.find((c) => catalogName && c.name.toLowerCase() === catalogName) ?? (subject ? bestMatch(active, subject, (c) => c.name) : undefined);
  const catId = catalogId ?? guess?.id ?? "";
  const prefilled = !!guess && catId === guess.id;
  const cat = active.find((c) => c.id === catId);
  const itemText = item.trim() || cat?.name || "";
  const addFiles = (list: FileList | File[] | null) => list && setFiles((cur) => [...cur, ...Array.from(list)].slice(0, 10));
  const prev = i > 0 ? () => setI(i - 1) : undefined;

  const send = useMutation({
    mutationFn: async () => {
      let p = await api<Purchase>("/api/workload/purchases", {
        method: "POST",
        json: {
          ticket_id: ticketId,
          source: source === "ticket" ? "manual" : source,
          source_ref: source === "onboard" ? ref : null,
          employee: employee.trim() || null,
          catalog_item_id: cat?.id ?? null,
          kind,
          item: itemText,
          quantity,
          reason,
        },
      });
      for (const f of files) p = await uploadEvidence(p.id, f);
      return p;
    },
    onSuccess: (p) => {
      setSent(p);
      announce(`Đã gửi đề xuất ${p.catalog_item?.name ?? p.item}.`);
      void qc.invalidateQueries({ queryKey: ["workload"] });
    },
    onError: (e) => {
      toast.error(errorMessage(e));
      void qc.invalidateQueries({ queryKey: ["workload"] });
    },
  });

  const header = (
    <>
      <nav aria-label="Đường dẫn">
        <Link to="/workload/purchases" className="mb-1 inline-flex items-center gap-1 text-sm text-muted hover:text-ink">
          <ChevronLeft className="size-4" aria-hidden="true" />Đề xuất mua thiết bị
        </Link>
      </nav>
      <PageHeader title="Đề xuất thiết bị cần cấp" description={fromLabel ? `Thông tin ${fromLabel} đã điền sẵn – kiểm tra và xác nhận.` : "Chỉ cần chọn thiết bị và đính kèm bằng chứng; Supervisor định giá khi duyệt."} />
    </>
  );

  if (sent) {
    return (
      <>
        {header}
        <div className="mx-auto max-w-3xl rounded-md border border-line bg-surface p-6 shadow-card">
          <EmptyState icon={CheckCircle2} title="Đã gửi đề xuất">
            “{sent.catalog_item?.name ?? sent.item} × {sent.quantity}” đang chờ Supervisor duyệt.
            {sent.evidence.length === 0 && " Nhớ bổ sung bằng chứng Function Head – chưa có bằng chứng thì chưa duyệt được."}
          </EmptyState>
          <div className="mt-2 flex flex-wrap justify-center gap-2">
            <Button onClick={() => navigate("/workload/purchases")}>Xem danh sách đề xuất</Button>
            {back && <Button onClick={() => navigate(back)}>Quay lại Job</Button>}
            <Button variant="primary" onClick={() => navigate("/me/work")}>Việc tiếp theo</Button>
          </div>
        </div>
      </>
    );
  }

  const key = STEPS[i]!.key;
  if (key === "device") {
    return (
      <>
        {header}
        <Wizard
          label="Đề xuất mua"
          steps={STEPS}
          current={i}
          title="Cần cấp thiết bị gì?"
          description={prefilled ? `Gợi ý ${fromLabel}. Sửa nếu chưa đúng.` : subject ? "Request chưa nêu thiết bị cụ thể – chọn trong danh sách hoặc mô tả." : "Chọn trong danh sách thiết bị, không có thì mô tả."}
          next={{ label: prefilled ? "Đúng – tiếp tục" : "Tiếp tục", onClick: () => setI(1), disabled: itemText.length < 2, reason: itemText.length < 2 ? "Chọn hoặc mô tả thiết bị." : null }}
        >
          {subject && (
            <p className="mb-3 rounded-sm bg-surface-2 px-3 py-2 text-sm">
              <span className="text-muted">Nội dung request #{ref || ticketId}:</span> {subject}
            </p>
          )}
          <div className="grid gap-3 sm:grid-cols-[1fr_9rem_7rem]">
            <Field label={<>Thiết bị{prefilled && <From>{catalogName ? fromLabel : "khớp nội dung request"}</From>}</>} className="sm:col-span-3">
              <Select data-autofocus value={catId} onChange={(e) => setCatalogId(e.target.value)}>
                <option value="">— Không có trong danh sách —</option>
                {active.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
              </Select>
            </Field>
            <Field label={cat ? "Ghi chú thêm (tuỳ chọn)" : "Mô tả thiết bị / linh kiện"} className="sm:col-span-3">
              <Input value={item} maxLength={300} onChange={(e) => setItem(e.target.value)} placeholder={cat?.name ?? "Ví dụ: RAM 16GB DDR5 cho laptop"} />
            </Field>
            <Field label="Loại">
              <Select value={kind} onChange={(e) => setKind(e.target.value as DeviceNeed | "new")}>
                {Object.entries(KIND_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}
              </Select>
            </Field>
            <Field label="Số lượng">
              <Input type="number" min={1} max={500} value={quantity} onChange={(e) => setQuantity(Math.max(1, Number(e.target.value) || 1))} />
            </Field>
          </div>
        </Wizard>
      </>
    );
  }

  if (key === "user") {
    return (
      <>
        {header}
        <Wizard
          label="Đề xuất mua"
          steps={STEPS}
          current={i}
          title="Cho ai và vì sao?"
          description="Supervisor và Finance đọc phần này khi duyệt."
          onBack={prev}
          next={{ label: "Đúng – tiếp tục", onClick: () => setI(2), disabled: !employee.trim(), reason: employee.trim() ? null : "Nhập người dùng thiết bị." }}
        >
          <div className="space-y-3">
            <Field label={<>Người dùng thiết bị{fromLabel && employee && <From>{fromLabel}</From>}</>}>
              <Input data-autofocus value={employee} maxLength={256} onChange={(e) => setEmployee(e.target.value)} />
            </Field>
            <Field label={<>Lý do{fromLabel && reason && <From>{fromLabel}</From>}</>} hint="Một câu là đủ, ví dụ: “Laptop cũ hỏng bản lề, đã quá hạn bảo hành”.">
              <Textarea rows={3} maxLength={2000} value={reason} onChange={(e) => setReason(e.target.value)} />
            </Field>
          </div>
        </Wizard>
      </>
    );
  }

  if (key === "evidence") {
    return (
      <>
        {header}
        <Wizard
          label="Đề xuất mua"
          steps={STEPS}
          current={i}
          title="Function Head của người dùng đã đồng ý chưa?"
          description={<>Đính kèm ảnh chụp email / tin nhắn / phiếu duyệt. Supervisor chỉ duyệt được khi có bằng chứng. <EvidenceHelp /></>}
          onBack={prev}
          aside={files.length === 0 && <Button variant="ghost" onClick={() => { setSkipEvidence(true); setI(3); }}>Chưa có – bổ sung sau</Button>}
          next={{ label: files.length ? `Dùng ${files.length} file – tiếp tục` : "Tiếp tục", onClick: () => { setSkipEvidence(false); setI(3); }, disabled: files.length === 0, reason: files.length ? null : "Chưa có bằng chứng." }}
        >
          <div onPaste={(e) => { const f = pastedImage(e); if (f) addFiles([f]); }} className="rounded-sm border border-dashed border-line-strong p-4 text-center">
            <label className="inline-flex h-10 cursor-pointer items-center gap-1.5 rounded-sm border border-line-strong bg-surface px-4 font-medium hover:bg-surface-2 focus-within:outline-2 focus-within:outline-primary">
              <Paperclip className="size-4" aria-hidden="true" />Chọn file / ảnh
              <input type="file" multiple accept={EVIDENCE_ACCEPT} className="sr-only" onChange={(e) => { addFiles(Array.from(e.target.files ?? [])); e.target.value = ""; }} />
            </label>
            <p className="mt-2 text-sm text-muted">PNG, JPG, PDF · hoặc bấm vào khung này rồi dán ảnh chụp (Ctrl+V)</p>
          </div>
          {files.length > 0 && (
            <ul className="mt-3 space-y-1 text-sm">
              {files.map((f, n) => (
                <li key={`${f.name}-${n}`} className="flex items-center gap-2">
                  <Paperclip className="size-3.5 text-muted" aria-hidden="true" />
                  <span className="truncate">{f.name}</span>
                  <Button size="icon" variant="ghost" aria-label={`Bỏ ${f.name}`} onClick={() => setFiles((cur) => cur.filter((_, k) => k !== n))}>
                    <X className="size-4" aria-hidden="true" />
                  </Button>
                </li>
              ))}
            </ul>
          )}
        </Wizard>
      </>
    );
  }

  return (
    <>
      {header}
      <Wizard
        label="Đề xuất mua"
        steps={STEPS}
        current={i}
        title="Xem lại và gửi đề xuất"
        description="Sau khi gửi, đề xuất chờ Supervisor duyệt; bạn sửa bằng chứng được đến khi duyệt."
        onBack={prev}
        next={{ label: <><Send className="size-4" aria-hidden="true" />Gửi đề xuất</>, onClick: () => send.mutate(), loading: send.isPending }}
      >
        <dl className="grid grid-cols-[10rem_1fr] gap-x-4 gap-y-1.5 text-sm">
          <dt className="text-muted">Thiết bị</dt><dd className="font-medium">{cat?.name ?? itemText}{cat && item.trim() && <span className="font-normal text-muted"> – {item.trim()}</span>}</dd>
          <dt className="text-muted">Loại · số lượng</dt><dd>{KIND_LABEL[kind]} · {quantity}</dd>
          <dt className="text-muted">Người dùng</dt><dd>{employee}</dd>
          <dt className="text-muted">Lý do</dt><dd className="whitespace-pre-line">{reason || "—"}</dd>
          {fromLabel && (<><dt className="text-muted">Nguồn</dt><dd>{ticketId ? <>Request <Mono>#{ref || ticketId}</Mono></> : <>Onboard <Mono>#{ref}</Mono></>}</dd></>)}
          <dt className="text-muted">Bằng chứng</dt>
          <dd className={files.length ? "" : "text-waiting"}>{files.length ? `${files.length} file` : skipEvidence ? "Chưa có – nhớ bổ sung trước khi Supervisor duyệt" : "Chưa có"}</dd>
        </dl>
      </Wizard>
    </>
  );
}
