import { RequirePermission } from "@/core/auth/guards";
import type { FrontendModule } from "@/core/modules";
import { SettingsPage } from "./pages/SettingsPage";
import { StatsPage } from "./pages/StatsPage";
import { TicketsPage } from "./pages/TicketsPage";
import { WorkloadNextWidget } from "./Widget";
import { MyTicketsSection } from "./MyTickets";
import { PurchasesPage } from "./pages/PurchasesPage";
import { NewPurchasePage } from "./pages/NewPurchasePage";
import { ReceivePage } from "./pages/ReceivePage";

/** Module 2 – Helpdesk/Support workload from ServiceDesk Plus. */
export const workloadModule: FrontendModule = {
  id: "workload",
  routes: [
    { path: "workload/tickets", element: <RequirePermission permission="workload.tickets.view"><TicketsPage /></RequirePermission> },
    { path: "workload/purchases/receive", element: <RequirePermission permission="workload.purchase.manage"><ReceivePage /></RequirePermission> },
    { path: "workload/purchases/new", element: <RequirePermission permission="workload.purchase.create"><NewPurchasePage /></RequirePermission> },
    { path: "workload/purchases", element: <RequirePermission permission="workload.tickets.view"><PurchasesPage /></RequirePermission> },
    { path: "workload/stats", element: <RequirePermission permission="workload.stats.view"><StatsPage /></RequirePermission> },
    { path: "workload/settings", element: <RequirePermission permission="workload.settings.manage"><SettingsPage /></RequirePermission> },
  ],
  widgets: { workload_next: WorkloadNextWidget },
  portal: [{ key: "workload.mine", order: 2, permission: "workload.tickets.view", component: MyTicketsSection }],
};
