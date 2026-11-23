import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Flowchart } from "./flowchart";

describe("Flowchart", () => {
  it("lists steps in order with checklist state and verification gates", () => {
    render(
      <Flowchart
        label="Lưu đồ thử"
        verify
        steps={[
          { key: "a", title: "Bước A", kind: "auto", writes: ["sdp"], confirm: false, checklist: [{ id: "c1", text: "Mục 1", checked: true }], evidenceRequired: true, status: "done", statusLabel: "Hoàn tất" },
          { key: "b", title: "Bước B", kind: "manual", writes: [], confirm: true, checklist: [], evidenceRequired: false, status: "pending", statusLabel: "Chưa chạy" },
        ]}
        systemLabel={{ sdp: "ServiceDesk Plus" }}
      />,
    );
    const items = within(screen.getByRole("list", { name: "Lưu đồ thử" })).getAllByRole("region");
    expect(items.map((i) => i.getAttribute("aria-label"))).toEqual(["Bước 1: Bước A", "Bước 2: Bước B"]);
    expect(within(items[0]!).getByText("Đã kiểm tra:")).toBeInTheDocument();
    expect(within(items[0]!).getByText("Cần bằng chứng")).toBeInTheDocument();
    expect(within(items[0]!).getByText("Ghi vào ServiceDesk Plus")).toBeInTheDocument();
    expect(within(items[1]!).getByText("Nhập lại Mã NV trước khi chạy")).toBeInTheDocument();
    expect(screen.getAllByText(/Operator kiểm tra/)).toHaveLength(2);
  });
});
