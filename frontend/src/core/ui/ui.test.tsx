import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Field, Input } from "./field";
import { StatusBadge } from "./status";
import { SystemRail } from "./system-rail";
import { ConfirmDangerDialog } from "./confirm-danger-dialog";
import { fold } from "@/core/lib/format";

beforeAll(() => {
  // jsdom lacks the <dialog> modal API.
  HTMLDialogElement.prototype.showModal = function () {
    this.setAttribute("open", "");
  };
  HTMLDialogElement.prototype.close = function () {
    this.removeAttribute("open");
  };
});

describe("Field", () => {
  it("does not expose the error until showError, then links it via aria-describedby", () => {
    const { rerender } = render(
      <Field label="Tên" hint="Gợi ý" error="Nhập tên." showError={false}>
        <Input />
      </Field>,
    );
    const input = screen.getByLabelText("Tên");
    expect(input).toHaveAttribute("aria-invalid", "false");
    expect(input.getAttribute("aria-describedby")?.split(" ")).toHaveLength(1);
    rerender(
      <Field label="Tên" hint="Gợi ý" error="Nhập tên." showError>
        <Input />
      </Field>,
    );
    expect(input).toHaveAttribute("aria-invalid", "true");
    expect(input).toHaveAccessibleDescription(/Gợi ý.*Nhập tên\./);
  });
});

describe("StatusBadge / SystemRail", () => {
  it("always renders a text label, never colour alone", () => {
    render(<StatusBadge status="mismatch" />);
    expect(screen.getByText("Lệch")).toBeInTheDocument();
  });
  it("announces each system cell", () => {
    render(
      <SystemRail
        cells={[
          { key: "sdp", label: "SDP", status: "match" },
          { key: "biostar", label: "BioStar", status: "failed", detail: "Thiếu user" },
        ]}
      />,
    );
    expect(screen.getByRole("list", { name: "Trạng thái theo hệ thống" })).toBeInTheDocument();
    expect(screen.getByText("BioStar: Lỗi – Thiếu user")).toBeInTheDocument();
  });
});

describe("ConfirmDangerDialog", () => {
  function Harness({ onConfirm }: { onConfirm: () => void }) {
    const [open, setOpen] = useState(true);
    return (
      <ConfirmDangerDialog
        open={open}
        onClose={() => setOpen(false)}
        onConfirm={onConfirm}
        title="Xoá kết nối?"
        consequences="Không khôi phục được."
        confirmText="003551"
        confirmLabel="Xoá"
      />
    );
  }
  it("enables the destructive button only after retyping the identifier", async () => {
    const onConfirm = vi.fn();
    render(<Harness onConfirm={onConfirm} />);
    const button = screen.getByRole("button", { name: "Xoá" });
    expect(button).toBeDisabled();
    await userEvent.type(screen.getByRole("textbox"), "3551");
    expect(button).toBeDisabled();
    await userEvent.clear(screen.getByRole("textbox"));
    await userEvent.type(screen.getByRole("textbox"), "003551");
    expect(button).toBeEnabled();
    await userEvent.click(button);
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});

describe("fold (diacritic-insensitive search)", () => {
  it("matches Vietnamese without accents", () => {
    expect(fold("Kết nối hệ thống")).toBe("ket noi he thong");
    expect(fold("Đối soát")).toBe("doi soat");
  });
});

describe("DateInput helpers", () => {
  it("parses only real dd/MM/yyyy dates", async () => {
    const { parseVnDate, formatTyped, toVnDate } = await import("./date-input");
    expect(parseVnDate("01/11/2026")).toBe("2026-11-01");
    expect(parseVnDate("1/2/2026")).toBe("2026-02-01");
    expect(parseVnDate("31/02/2026")).toBeNull();
    expect(parseVnDate("2026-11-01")).toBeNull();
    expect(formatTyped("01112026", true)).toBe("01/11/2026");
    expect(formatTyped("01", true)).toBe("01/");
    expect(formatTyped("0111", true)).toBe("01/11/");
    expect(formatTyped("01/1", false)).toBe("01/1");
    expect(toVnDate("2026-11-01")).toBe("01/11/2026");
  });
});


describe("Dialog focus", () => {
  it("focuses the field marked data-autofocus, not the close button", async () => {
    const { Dialog } = await import("./dialog");
    render(
      <Dialog open onClose={() => {}} title="Đi tới">
        <input aria-label="Tìm trang" data-autofocus />
      </Dialog>,
    );
    expect(document.activeElement).toBe(screen.getByLabelText("Tìm trang"));
  });
});

describe("Field keeps the control's own id", () => {
  it("links the label to a caller-provided id", () => {
    render(
      <Field label="Serial máy 1">
        <Input id="serial-1" />
      </Field>,
    );
    expect(screen.getByLabelText("Serial máy 1")).toHaveAttribute("id", "serial-1");
  });
});
