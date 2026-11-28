import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TruncationNotice } from "./TruncationNotice";

describe("TruncationNotice", () => {
  it("tells how many rows are hidden", () => {
    render(<TruncationNotice shown={500} total={812} />);
    expect(screen.getByRole("status").textContent).toContain("Đang hiện 500 / 812");
  });
  it("is silent when the list is complete or total is unknown", () => {
    const { container } = render(<><TruncationNotice shown={5} total={5} /><TruncationNotice shown={5} /></>);
    expect(container.textContent).toBe("");
  });
});
