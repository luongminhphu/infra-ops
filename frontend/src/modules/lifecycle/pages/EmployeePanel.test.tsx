import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

const perms = vi.hoisted(() => ({ has: true }));
const GRANTED = new Set(["lifecycle.reconciliation.view", "lifecycle.job.view"]);
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: (p: string) => perms.has && GRANTED.has(p) }) }));

import { AssetTagLink, EmpLink } from "./EmployeePanel";

const ui = () => render(<MemoryRouter><EmpLink id="003551" /> <AssetTagLink tag="A/B 1" /></MemoryRouter>);

describe("cross-links", () => {
  it("link with permission, keeping leading zeros and encoding params", () => {
    perms.has = true;
    ui();
    expect(screen.getByRole("link", { name: "003551" }).getAttribute("href")).toBe("/lifecycle/reconciliation?emp=003551");
    expect(screen.getByRole("link", { name: "A/B 1" }).getAttribute("href")).toBe("/lifecycle/stock?tab=assets&q=A%2FB%201");
  });
  it("plain text without permission", () => {
    perms.has = false;
    ui();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/003551/)).toBeTruthy();
  });
});
