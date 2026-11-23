import { RequirePermission } from "@/core/auth/guards";
import type { FrontendModule } from "@/core/modules";
import { DocumentPage } from "./pages/DocumentPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { FlowsPage } from "./pages/FlowsPage";
import { JobPage } from "./pages/JobPage";
import { JobsPage } from "./pages/JobsPage";
import { ReconciliationPage } from "./pages/ReconciliationPage";
import { TemplatesPage } from "./pages/TemplatesPage";
import { RequestsPage } from "./pages/RequestsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SyncPage } from "./pages/SyncPage";
import { JobsWidget, ReconciliationWidget } from "./Widget";

/** Module 1 – IT Lifecycle: SDP + BioStar reconciliation, handover/return records. */
export const lifecycleModule: FrontendModule = {
  id: "lifecycle",
  routes: [
    { path: "lifecycle/jobs", element: <RequirePermission permission="lifecycle.job.view"><JobsPage /></RequirePermission> },
    { path: "lifecycle/flows", element: <RequirePermission permission="lifecycle.job.view"><FlowsPage /></RequirePermission> },
    { path: "lifecycle/jobs/:id", element: <RequirePermission permission="lifecycle.job.view"><JobPage /></RequirePermission> },
    { path: "lifecycle/reconciliation", element: <RequirePermission permission="lifecycle.reconciliation.view"><ReconciliationPage /></RequirePermission> },
    { path: "lifecycle/requests", element: <RequirePermission permission="lifecycle.reconciliation.view"><RequestsPage /></RequirePermission> },
    { path: "lifecycle/sync", element: <RequirePermission permission="lifecycle.reconciliation.view"><SyncPage /></RequirePermission> },
    { path: "lifecycle/documents", element: <RequirePermission permission="lifecycle.documents.view"><DocumentsPage /></RequirePermission> },
    { path: "lifecycle/documents/new", element: <RequirePermission permission="lifecycle.documents.create"><DocumentPage /></RequirePermission> },
    { path: "lifecycle/documents/:id", element: <RequirePermission permission="lifecycle.documents.view"><DocumentPage key="doc" /></RequirePermission> },
    { path: "lifecycle/templates", element: <RequirePermission permission="lifecycle.templates.manage"><TemplatesPage /></RequirePermission> },
    { path: "lifecycle/settings", element: <RequirePermission permission="lifecycle.settings.manage"><SettingsPage /></RequirePermission> },
  ],
  widgets: { lifecycle_reconciliation: ReconciliationWidget, lifecycle_jobs: JobsWidget },
};
