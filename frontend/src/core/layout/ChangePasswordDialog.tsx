import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";

export function ChangePasswordDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [saving, setSaving] = useState(false);
  const [serverError, setServerError] = useState("");

  useEffect(() => {
    if (open) {
      setCurrent("");
      setNext("");
      setConfirm("");
      setSubmitted(false);
      setServerError("");
    }
  }, [open]);

  const mismatch = confirm && next !== confirm ? "Mật khẩu nhập lại không khớp." : "";
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (!current || !next || mismatch) {
      announce("Vui lòng sửa các trường được đánh dấu.");
      return;
    }
    setSaving(true);
    try {
      await api("/api/core/me/password", { method: "POST", json: { current_password: current, new_password: next } });
      toast.success("Đã đổi mật khẩu.");
      onClose();
    } catch (err) {
      setServerError(errorMessage(err));
      announce(errorMessage(err));
    } finally {
      setSaving(false);
    }
  };

  return (
    <Dialog open={open} onClose={onClose} title="Đổi mật khẩu" size="sm">
      <form onSubmit={submit} className="space-y-3" noValidate>
        <Field label="Mật khẩu hiện tại" required error={!current ? "Nhập mật khẩu hiện tại." : undefined} showError={submitted}>
          <Input type="password" autoComplete="current-password" value={current} onChange={(e) => setCurrent(e.target.value)} />
        </Field>
        <Field
          label="Mật khẩu mới"
          required
          hint="Tối thiểu 12 ký tự, gồm ít nhất 3 loại: chữ thường, chữ hoa, số, ký tự đặc biệt."
          error={!next ? "Nhập mật khẩu mới." : undefined}
          showError={submitted}
        >
          <Input type="password" autoComplete="new-password" value={next} onChange={(e) => setNext(e.target.value)} />
        </Field>
        <Field label="Nhập lại mật khẩu mới" required error={mismatch || undefined} showError={submitted || !!confirm}>
          <Input type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
        </Field>
        {serverError && (
          <p role="alert" className="text-sm text-failed">
            {serverError}
          </p>
        )}
        <div className="flex justify-end gap-2 pt-2">
          <Button onClick={onClose}>Huỷ</Button>
          <Button type="submit" variant="primary" loading={saving}>
            Đổi mật khẩu
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
