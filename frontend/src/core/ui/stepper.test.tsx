import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Stepper } from "./stepper";

describe("Stepper", () => {
  it("labels every step status in text and marks the selected step", async () => {
    const onSelect = vi.fn();
    render(
      <Stepper
        label="Các bước"
        selected="b"
        onSelect={onSelect}
        steps={[
          { key: "a", title: "Kiểm tra request", status: "done", statusLabel: "Hoàn tất" },
          { key: "b", title: "Gán tài sản", status: "failed", statusLabel: "Lỗi" },
        ]}
      />,
    );
    expect(screen.getByRole("list", { name: "Các bước" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Gán tài sản.*Lỗi/ })).toHaveAttribute("aria-current", "step");
    await userEvent.click(screen.getByRole("button", { name: /Kiểm tra request/ }));
    expect(onSelect).toHaveBeenCalledWith("a");
  });
});
