import type { ReactNode } from "react";
import { Dialog } from "./dialog";
import { Button } from "./button";

interface Props {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  confirmLabel: string;
  children: ReactNode;
}

/** Plain confirmation for destructive but low-stakes actions (no identifier retyping – see ConfirmDangerDialog). */
export function ConfirmDialog({ open, onClose, onConfirm, title, confirmLabel, children }: Props) {
  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={title}
      size="sm"
      footer={
        <>
          <Button onClick={onClose}>Huỷ</Button>
          <Button variant="danger" onClick={onConfirm} data-autofocus>
            {confirmLabel}
          </Button>
        </>
      }
    >
      <p className="text-sm">{children}</p>
    </Dialog>
  );
}
