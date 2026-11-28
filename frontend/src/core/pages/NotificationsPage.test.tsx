import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { beforeEach, describe, expect, it, vi } from "vitest";

const apiMock = vi.hoisted(() => vi.fn());
const canMock = vi.hoisted(() => vi.fn());
vi.mock("@/core/api/client", () => ({ api: apiMock, errorMessage: String }));
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: canMock }) }));

import { NotificationsPage } from "./NotificationsPage";

const settings = { enabled: false, times: ["08:00"], smtp_profile_id: null, teams_profile_id: null, max_per_run: 200, app_base_url: "" };

const renderPage = () =>
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <NotificationsPage />
    </QueryClientProvider>,
  );

describe("NotificationsPage", () => {
  beforeEach(() => {
    apiMock.mockReset();
    apiMock.mockImplementation(async (url: string) => {
      if (url.endsWith("/me/notifications")) return { email_digest: false, has_email: true, enabled: true, times: ["08:00"] };
      if (url.endsWith("/profiles")) return { smtp: [], teams: [] };
      return settings;
    });
  });

  it("hides the administrator section without core.notifications.manage", async () => {
    canMock.mockReturnValue(false);
    renderPage();
    expect(await screen.findByRole("checkbox", { name: "Gửi thư tóm tắt cho tôi" })).toBeTruthy();
    expect(screen.queryByText("Cấu hình gửi thông báo (quản trị)")).toBeNull();
    expect(apiMock).not.toHaveBeenCalledWith("/api/core/notifications/settings");
  });

  it("warns when the account has no e-mail address", async () => {
    canMock.mockReturnValue(false);
    apiMock.mockImplementation(async () => ({ email_digest: true, has_email: false, enabled: true, times: ["08:00"] }));
    renderPage();
    expect(await screen.findByText(/chưa có địa chỉ e-mail/)).toBeTruthy();
  });

  it("shows the administrator section with core.notifications.manage", async () => {
    canMock.mockReturnValue(true);
    renderPage();
    expect(await screen.findByText("Cấu hình gửi thông báo (quản trị)")).toBeTruthy();
    expect(await screen.findByRole("button", { name: "Lưu cấu hình" })).toBeTruthy();
  });
});
