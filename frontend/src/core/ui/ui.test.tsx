import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { Field, Input } from "./field";
import { StatusBadge } from "./status";
import { SystemRail } from "./system-rail";
import { ConfirmDangerDialog } from "./confirm-danger-dialog";
import { ConfirmDialog } from "./confirm-dialog";
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

  it("builds Monday-first month grids and shifts by day and month", async () => {
    const { monthGrid, shiftDate } = await import("./date-input");
    const nov = monthGrid(2026, 10); // November 2026 starts on a Sunday
    expect(nov[0]).toBe("2026-10-26");
    expect(nov[6]).toBe("2026-11-01");
    expect(nov).toHaveLength(42);
    expect(shiftDate("2026-11-30", 1)).toBe("2026-12-01");
    expect(shiftDate("2026-01-31", 0, 1)).toBe("2026-02-28"); // clamped to the month's last day
    expect(shiftDate("2026-03-01", -7)).toBe("2026-02-22");
  });

  it("picks a date on the calendar and still accepts typing", async () => {
    const { DateInput } = await import("./date-input");
    const seen: string[] = [];
    function Host() {
      const [v, setV] = useState("2026-11-01");
      return (
        <Field label="Từ ngày">
          <DateInput value={v} onChange={(d) => { setV(d); seen.push(d); }} />
        </Field>
      );
    }
    const user = userEvent.setup();
    render(<Host />);
    await user.click(screen.getByRole("button", { name: "Chọn ngày trên lịch" }));
    expect(screen.getByText("Tháng 11/2026")).toBeInTheDocument();
    await user.click(screen.getByRole("gridcell", { name: /^15\/11\/2026/ }));
    expect(screen.getByLabelText("Từ ngày")).toHaveValue("15/11/2026");
    expect(screen.queryByRole("dialog", { name: "Chọn ngày" })).not.toBeInTheDocument();
    // Keyboard: open, move a week down, pick with Enter.
    await user.click(screen.getByRole("button", { name: "Chọn ngày trên lịch" }));
    await user.keyboard("{ArrowDown}{Enter}");
    expect(seen.at(-1)).toBe("2026-11-22");
    await user.clear(screen.getByLabelText("Từ ngày"));
    await user.type(screen.getByLabelText("Từ ngày"), "03122026");
    expect(seen.at(-1)).toBe("2026-12-03");
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

describe("Field aria-describedby", () => {
  it("keeps the control's own aria-describedby next to the hint id", () => {
    render(
      <>
        <p id="extra">Thêm</p>
        <Field label="Tên" hint="Gợi ý">
          <Input aria-describedby="extra" />
        </Field>
      </>,
    );
    const ids = screen.getByLabelText("Tên").getAttribute("aria-describedby")!.split(" ");
    expect(ids).toContain("extra");
    expect(ids).toHaveLength(2);
  });

  it("leaves it unset when there is nothing to describe", () => {
    render(<Field label="Tên"><Input /></Field>);
    expect(screen.getByLabelText("Tên")).not.toHaveAttribute("aria-describedby");
  });
});

describe("ConfirmDialog", () => {
  it("runs the action only after the confirm button is pressed", async () => {
    const onConfirm = vi.fn();
    render(<ConfirmDialog open onClose={() => {}} onConfirm={onConfirm} title="Xoá thử" confirmLabel="Xoá ngay">Nội dung</ConfirmDialog>);
    expect(onConfirm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Xoá ngay" }));
    expect(onConfirm).toHaveBeenCalledOnce();
  });
});
