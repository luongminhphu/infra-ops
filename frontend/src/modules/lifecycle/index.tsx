import { Navigate } from "react-router-dom";
import { RequirePermission } from "@/core/auth/guards";
import type { FrontendModule } from "@/core/modules";
import { AttendancePage } from "./pages/AttendancePage";
import { DocumentPage } from "./pages/DocumentPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { FlowsPage } from "./pages/FlowsPage";
import { StockPage } from "./pages/StockPage";
import { JobPage } from "./pages/JobPage";
import { JobsPage } from "./pages/JobsPage";
import { OffboardReviewPage } from "./pages/OffboardReviewPage";
import { ReconciliationPage } from "./pages/ReconciliationPage";
import { TemplatesPage } from "./pages/TemplatesPage";
import { RequestsPage } from "./pages/RequestsPage";
import { SettingsPage } from "./pages/SettingsPage";
import { SyncPage } from "./pages/SyncPage";
import { StocktakePage, StocktakesPage } from "./pages/StocktakePage";
import { JobsWidget, ReconciliationWidget } from "./Widget";
import { MyJobsSection } from "./MyJobs";
import { TeamQueueWidget } from "./TeamQueue";

/** Module 1 – IT Lifecycle: SDP + BioStar reconciliation, handover/return records. */
export const lifecycleModule: FrontendModule = {
  id: "lifecycle",
  routes: [
    { path: "lifecycle/jobs", element: <RequirePermission permission="lifecycle.job.view"><JobsPage /></RequirePermission> },
    { path: "lifecycle/flows", element: <RequirePermission permission="core.workflow.checklist.manage"><FlowsPage /></RequirePermission> },
    // The allocation and stock pages are one page now: keep old links working.
    { path: "lifecycle/inventory", element: <Navigate to="/lifecycle/stock?tab=onboard" replace /> },
    { path: "lifecycle/stock", element: <RequirePermission permission="lifecycle.job.view"><StockPage /></RequirePermission> },
    { path: "lifecycle/jobs/:id", element: <RequirePermission permission="lifecycle.job.view"><JobPage /></RequirePermission> },
    { path: "lifecycle/reconciliation", element: <RequirePermission permission="lifecycle.reconciliation.view"><ReconciliationPage /></RequirePermission> },
    { path: "lifecycle/requests", element: <RequirePermission permission="lifecycle.reconciliation.view"><RequestsPage /></RequirePermission> },
    { path: "lifecycle/sync", element: <RequirePermission permission="lifecycle.reconciliation.view"><SyncPage /></RequirePermission> },
    { path: "lifecycle/documents", element: <RequirePermission permission="lifecycle.documents.view"><DocumentsPage /></RequirePermission> },
    { path: "lifecycle/documents/new", element: <RequirePermission permission="lifecycle.documents.create"><DocumentPage /></RequirePermission> },
    { path: "lifecycle/documents/:id", element: <RequirePermission permission="lifecycle.documents.view"><DocumentPage key="doc" /></RequirePermission> },
    { path: "lifecycle/attendance", element: <RequirePermission permission="lifecycle.attendance.export"><AttendancePage /></RequirePermission> },
    { path: "lifecycle/stocktake", element: <RequirePermission permission="lifecycle.stocktake.view"><StocktakesPage /></RequirePermission> },
    { path: "lifecycle/stocktake/:id", element: <RequirePermission permission="lifecycle.stocktake.view"><StocktakePage /></RequirePermission> },
    { path: "lifecycle/offboard-review", element: <RequirePermission permission="lifecycle.offboard_review.export"><OffboardReviewPage /></RequirePermission> },
    { path: "lifecycle/templates", element: <RequirePermission permission="lifecycle.templates.manage"><TemplatesPage /></RequirePermission> },
    { path: "lifecycle/settings", element: <RequirePermission permission="lifecycle.settings.manage"><SettingsPage /></RequirePermission> },
  ],
  widgets: { lifecycle_team_queue: TeamQueueWidget, lifecycle_reconciliation: ReconciliationWidget, lifecycle_jobs: JobsWidget },
  portal: [{ key: "lifecycle.jobs", order: 1, permission: "lifecycle.job.view", component: MyJobsSection }],
};
