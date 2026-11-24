import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { Wizard } from "./wizard";

const steps = [{ key: "a", title: "Thông tin" }, { key: "b", title: "Thiết bị" }, { key: "c", title: "Bắt đầu" }];

describe("Wizard", () => {
  it("marks the current step, runs the primary action and moves focus to the new question", () => {
    const next = vi.fn();
    const { rerender } = render(
      <Wizard label="Chuẩn bị" steps={steps} current={0} title="Thông tin có đúng không?" next={{ label: "Đúng – tiếp tục", onClick: next }}>
        nội dung
      </Wizard>,
    );
    expect(screen.getByText("Thông tin").closest("li")).toHaveAttribute("aria-current", "step");
    fireEvent.click(screen.getByRole("button", { name: "Đúng – tiếp tục" }));
    expect(next).toHaveBeenCalledOnce();
    rerender(
      <Wizard label="Chuẩn bị" steps={steps} current={1} title="Thiết bị cấp phát" onBack={() => {}} next={{ label: "Tiếp", onClick: next, disabled: true, reason: "Chưa chọn thiết bị." }}>
        nội dung
      </Wizard>,
    );
    expect(screen.getByRole("heading", { name: "Thiết bị cấp phát" })).toHaveFocus();
    expect(screen.getByRole("button", { name: "Tiếp" })).toHaveAccessibleDescription("Chưa chọn thiết bị.");
    expect(screen.getByRole("button", { name: /Quay lại/ })).toBeInTheDocument();
  });
});
