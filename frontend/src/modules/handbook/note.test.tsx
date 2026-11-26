import { describe, expect, it } from "vitest";
import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { Note, safeHref, safeImage } from "./Note";

describe("handbook note", () => {
  it("renders formatting, lists, links and store images; never scripts", () => {
    const { container } = render(
      <MemoryRouter>
        <Note text={"Kiểm tra **cáp** và `ipconfig`\n\n- Bước 1\n- [Mở Job](/lifecycle/jobs)\n\n![ảnh](/api/handbook/images/0f8fad5b-d9cb-469f-a165-70867728950e)\n\n[x](javascript:alert(1)) <script>alert(1)</script>"} />
      </MemoryRouter>,
    );
    expect(container.querySelector("strong")?.textContent).toBe("cáp");
    expect(container.querySelectorAll("li")).toHaveLength(2);
    expect(container.querySelector("a")?.getAttribute("href")).toBe("/lifecycle/jobs");
    expect(container.querySelector("img")?.getAttribute("src")).toContain("/api/handbook/images/");
    expect(container.querySelector("script")).toBeNull();
    expect(container.textContent).toContain("<script>");
    expect(safeHref("javascript:alert(1)")).toBeNull();
    expect(safeImage("http://evil/x.png")).toBeNull();
  });
});
