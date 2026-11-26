import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

vi.mock("@/modules", () => ({ modules: [{ id: "x", routes: [] }, { id: "playbook", routes: [], home: () => <p>PLAYBOOK</p> }] }));
vi.mock("./DashboardPage", () => ({ DashboardPage: () => <p>TỔNG QUAN</p> }));

const { HomePage } = await import("./HomePage");

describe("HomePage", () => {
  it("opens on the entry module's page (Playbook) for everyone", () => {
    render(<HomePage />);
    expect(screen.getByText("PLAYBOOK")).toBeInTheDocument();
  });
});
