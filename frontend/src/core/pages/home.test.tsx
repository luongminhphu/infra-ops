import { StrictMode } from "react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

const auth = { me: null as null | { roles: string[] } };
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ me: auth.me, can: () => true }) }));
// The dashboard itself is not under test here.
vi.mock("./DashboardPage", () => ({ DashboardPage: () => <p>TỔNG QUAN</p> }));

const { HomePage } = await import("./HomePage");
const { resetLanding } = await import("@/core/auth/landing");

function visitHome() {
  return render(
    <StrictMode>
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/me/work" element={<p>VIỆC CỦA TÔI</p>} />
        </Routes>
      </MemoryRouter>
    </StrictMode>,
  );
}

describe("HomePage landing", () => {
  beforeEach(() => resetLanding());

  it("sends an Operator to Việc của tôi once per session, then shows Tổng quan", () => {
    auth.me = { roles: ["operator"] };
    visitHome().unmount();
    expect(screen.queryByText("TỔNG QUAN")).toBeNull();
    const first = visitHome();
    expect(screen.getByText("TỔNG QUAN")).toBeInTheDocument();
    first.unmount();
    resetLanding(); // sign-in page: the next visit lands on Việc của tôi again
    visitHome();
    expect(screen.getByText("VIỆC CỦA TÔI")).toBeInTheDocument();
  });

  it("keeps Tổng quan for supervisors and above", () => {
    auth.me = { roles: ["operator", "supervisor"] };
    visitHome();
    expect(screen.getByText("TỔNG QUAN")).toBeInTheDocument();
  });
});
