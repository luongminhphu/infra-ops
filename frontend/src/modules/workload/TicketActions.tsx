import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, CircleCheck, MessageSquarePlus, ShoppingCart } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input, Textarea } from "@/core/ui/field";
import { Menu, MenuItem } from "@/core/ui/menu";
import { useTechnicians, type Ticket } from "./api";

type Action = "note" | "close";

/** Job page (only for people who may view Jobs). */
const jobUrl = (id: string) => `/lifecycle/jobs/${id}`;

function ActionDialog({ ticket, action, onClose }: { ticket: Ticket; action: Action; onClose: () => void }) {
  const qc = useQueryClient();
  const [text, setText] = useState("");
  const [touched, setTouched] = useState(false);
  const { can } = useAuth();
  const [job, setJob] = useState<{ id: string; params: { close_status?: string } } | null>(null);
  const [typed, setTyped] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const isClose = action === "close";
  const min = isClose ? 5 : 1;
  const ref = ticket.display_id ?? ticket.id;
  const submit = useMutation({
    mutationFn: () =>
      api<{ id: string; params: { close_status?: string } }>(`/api/workload/tickets/${ticket.id}/${action}`, {
        method: "POST",
        json: isClose ? { result: text.trim() } : { text: text.trim() },
      }),
    onSuccess: (created) => {
      void qc.invalidateQueries({ queryKey: ["workload"] });
      if (isClose) setJob(created);
      else {
        toast.success(`Đang thêm ghi chú vào request #${ref} trên SDP bằng khoá API của bạn.`);
        onClose();
      }
    },
  });
  const confirm = useMutation({
    mutationFn: () =>
      api(`/api/core/jobs/${job?.id}/steps/status/confirm`, { method: "POST", json: { typed: typed.trim() } }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["workload"] });
      setConfirmed(true);
    },
  });
  const blocking = (submit.error as { details?: { job_id?: string } } | null)?.details?.job_id;
  const invalid = text.trim().length < min;
  return (
    <Dialog
      open
      onClose={onClose}
      title={isClose ? `Đóng request #${ref}` : `Thêm ghi chú vào request #${ref}`}
      description={
        isClose
          ? "Ghi chú kết quả được thêm vào request, sau đó trạng thái trên ServiceDesk Plus đổi sang trạng thái đóng đã cấu hình (mặc định “Resolved”). Thao tác chạy bằng khoá API của bạn và không hoàn tác tự động."
          : "Ghi chú nội bộ (người yêu cầu không thấy) được thêm vào request trên ServiceDesk Plus bằng khoá API của bạn."
      }
      footer={
        job ? (
          <>
            <Button onClick={onClose}>{confirmed ? "Đóng" : "Để sau"}</Button>
            {!confirmed && (
              <Button variant="primary" loading={confirm.isPending} disabled={typed.trim() === ""} onClick={() => confirm.mutate()}>
                Xác nhận đóng
              </Button>
            )}
          </>
        ) : (
          <>
            <Button onClick={onClose}>Huỷ</Button>
            <Button
              variant="primary"
              loading={submit.isPending}
              onClick={() => {
                setTouched(true);
                if (!invalid) submit.mutate();
              }}
            >
              {isClose ? "Tạo yêu cầu đóng" : "Thêm ghi chú"}
            </Button>
          </>
        )
      }
    >
      {job ? (
        <div className="space-y-3">
          <p role="status" className="flex items-start gap-2 text-sm">
            <CircleCheck className="mt-0.5 size-4 shrink-0 text-done" aria-hidden="true" />
            <span>
              {confirmed
                ? `Đã xác nhận. Trạng thái request sẽ đổi sang “${job.params.close_status ?? ""}” trên ServiceDesk Plus trong ít phút.`
                : `Đã tạo Job đóng request. Ghi chú kết quả sẽ được gửi ngay; xác nhận để đổi trạng thái sang “${job.params.close_status ?? ""}”.`}{" "}
              {can("lifecycle.job.view") && (
                <Link className="font-medium text-primary hover:underline" to={jobUrl(job.id)}>Mở Job</Link>
              )}
            </span>
          </p>
          {!confirmed && (
            <Field label="Số request" required hint={`Nhập số request ${ref} để xác nhận đóng.`}>
              <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" />
            </Field>
          )}
          {confirm.isError && <p role="alert" className="text-sm text-failed">{errorMessage(confirm.error)}</p>}
        </div>
      ) : (
        <div className="space-y-3">
          <Field
            label={isClose ? "Kết quả xử lý" : "Nội dung ghi chú"}
            required
            hint={isClose ? "Tối thiểu 5 ký tự. Ví dụ: đã thay chuột, khách xác nhận dùng bình thường." : undefined}
            error={`Nhập ${isClose ? "kết quả xử lý (tối thiểu 5 ký tự)" : "nội dung ghi chú"}.`}
            showError={touched && invalid}
          >
            <Textarea rows={4} maxLength={2000} data-autofocus value={text} onChange={(e) => setText(e.target.value)} onBlur={() => setTouched(true)} />
          </Field>
          {submit.isError && (
            <p role="alert" className="text-sm text-failed">
              {errorMessage(submit.error)}{" "}
              {blocking && <Link className="font-medium underline" to={jobUrl(blocking)}>Mở Job đang chạy</Link>}
            </p>
          )}
        </div>
      )}
    </Dialog>
  );
}

/** "Thao tác" menu of one ticket: only for people who may update and only for their own open tickets. */
export function TicketActions({ ticket }: { ticket: Ticket }) {
  const { can } = useAuth();
  const techs = useTechnicians();
  const navigate = useNavigate();
  const [action, setAction] = useState<Action | null>(null);
  const mine = can("workload.tickets.assign") || (techs.data?.me != null && techs.data.me === ticket.technician_id);
  const ref = ticket.display_id ?? ticket.id;
  if (!can("workload.tickets.update") || ticket.state !== "open" || !mine) return null;
  const purchase = new URLSearchParams({
    ticket: ticket.id,
    ref,
    kind: ticket.device_need ?? "replace",
    item: ticket.subject.slice(0, 300),
    employee: ticket.requester_name ?? "",
    reason: `Request #${ref}: ${ticket.subject}`,
  });
  return (
    <>
      <Menu
        label={`Thao tác với request ${ref}`}
        trigger={(p) => (
          <Button size="sm" {...p}>
            Thao tác
            <ChevronDown className="size-3.5" aria-hidden="true" />
          </Button>
        )}
      >
        <MenuItem onSelect={() => setAction("note")}>
          <MessageSquarePlus className="size-4" aria-hidden="true" />
          Thêm ghi chú
        </MenuItem>
        <MenuItem onSelect={() => setAction("close")}>
          <CircleCheck className="size-4" aria-hidden="true" />
          Đóng request
        </MenuItem>
        {can("workload.purchase.create") && (
          <MenuItem onSelect={() => navigate(`/workload/purchases/new?${purchase}`)}>
            <ShoppingCart className="size-4" aria-hidden="true" />
            Tạo đề xuất mua
          </MenuItem>
        )}
      </Menu>
      {action && <ActionDialog ticket={ticket} action={action} onClose={() => setAction(null)} />}
    </>
  );
}
