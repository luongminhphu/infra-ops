import { RequirePermission } from "@/core/auth/guards";
import type { FrontendModule } from "@/core/modules";
import { ReconciliationPage } from "./pages/ReconciliationPage";
import { RequestsPage } from "./pages/RequestsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SyncPage } from "./pages/SyncPage";
import { ReconciliationWidget } from "./Widget";

/** Module 1 – IT Lifecycle. Phase 2: SDP + BioStar data and reconciliation. */
export const lifecycleModule: FrontendModule = {
  id: "lifecycle",
  routes: [
    { path: "lifecycle/reconciliation", element: <RequirePermission permission="lifecycle.reconciliation.view"><ReconciliationPage /></RequirePermission> },
    { path: "lifecycle/requests", element: <RequirePermission permission="lifecycle.reconciliation.view"><RequestsPage /></RequirePermission> },
    { path: "lifecycle/sync", element: <RequirePermission permission="lifecycle.reconciliation.view"><SyncPage /></RequirePermission> },
    { path: "lifecycle/settings", element: <RequirePermission permission="lifecycle.settings.manage"><SettingsPage /></RequirePermission> },
  ],
  widgets: { lifecycle_reconciliation: ReconciliationWidget },
};
