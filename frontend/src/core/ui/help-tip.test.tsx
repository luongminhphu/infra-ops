import { describe, expect, it } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { HelpTip } from "./help-tip";
import { formatVnd } from "@/core/lib/format";

describe("HelpTip", () => {
  it("opens on click, is linked to its button and closes on Escape", () => {
    render(<HelpTip title="Chốt PO">Hướng dẫn chốt</HelpTip>);
    const btn = screen.getByRole("button", { name: "Hướng dẫn: Chốt PO" });
    expect(btn).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(btn);
    const note = screen.getByRole("note", { name: "Chốt PO" });
    expect(note).toHaveTextContent("Hướng dẫn chốt");
    expect(btn).toHaveAttribute("aria-controls", note.id);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("note")).toBeNull();
    expect(btn).toHaveFocus();
  });
});

describe("formatVnd", () => {
  it("groups thousands with dots", () => {
    expect(formatVnd(200000000)).toBe("200.000.000 đ");
    expect(formatVnd(null)).toBe("—");
  });
});
