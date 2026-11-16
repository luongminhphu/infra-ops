import { useEffect, useState, type ReactNode } from "react";
import { TriangleAlert } from "lucide-react";
import { Dialog } from "./dialog";
import { Button } from "./button";
import { Field, Input } from "./field";

interface Props {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  /** What will happen, in concrete terms. */
  consequences: ReactNode;
  /** The value the user must type to unlock the action (e.g. employee ID or connection name). */
  confirmText: string;
  confirmLabel: string;
  inputLabel?: string;
  loading?: boolean;
}

/** Destructive action confirmation: states the consequences and requires retyping an identifier. */
export function ConfirmDangerDialog({
  open,
  onClose,
  onConfirm,
  title,
  consequences,
  confirmText,
  confirmLabel,
  inputLabel,
  loading,
}: Props) {
  const [typed, setTyped] = useState("");
  useEffect(() => {
    if (open) setTyped("");
  }, [open]);
  const matches = typed.trim() === confirmText;
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="danger" disabled={!matches} loading={loading} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <div className="flex gap-3 rounded-sm border border-failed/30 bg-failed-soft p-3 text-sm">
        <TriangleAlert className="mt-0.5 size-4 shrink-0 text-failed" aria-hidden="true" />
        <div className="space-y-1">{consequences}</div>
      </div>
      <form
        className="mt-4"
        onSubmit={(e) => {
          e.preventDefault();
          if (matches) onConfirm();
        }}
      >
        <Field label={inputLabel ?? <>Nhập <strong className="font-mono">{confirmText}</strong> để xác nhận</>}>
          <Input value={typed} onChange={(e) => setTyped(e.target.value)} autoComplete="off" spellCheck={false} />
        </Field>
      </form>
    </Dialog>
  );
}
