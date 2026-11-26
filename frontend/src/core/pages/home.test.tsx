import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

import { ModulesContext, type FrontendModule } from "@/core/modules";

vi.mock("./DashboardPage", () => ({ DashboardPage: () => <p>TỔNG QUAN</p> }));

const { HomePage } = await import("./HomePage");
const modules: FrontendModule[] = [{ id: "x", routes: [] }, { id: "playbook", routes: [], home: () => <p>PLAYBOOK</p> }];

describe("HomePage", () => {
  it("opens on the entry module's page (Playbook) for everyone", () => {
    render(<ModulesContext.Provider value={modules}><HomePage /></ModulesContext.Provider>);
    expect(screen.getByText("PLAYBOOK")).toBeInTheDocument();
  });

  it("falls back to the overview when no module provides a home", () => {
    render(<HomePage />);
    expect(screen.getByText("TỔNG QUAN")).toBeInTheDocument();
  });
});
