import { Navigate, useLocation } from "react-router-dom";
import { RequirePermission } from "@/core/auth/guards";
import type { FrontendModule } from "@/core/modules";
import { GuideEditorPage } from "./pages/GuideEditorPage";
import { GuidePage } from "./pages/GuidePage";
import { GuidesPage } from "./pages/GuidesPage";
import { StepGuidePage } from "./pages/StepGuidePage";
import { PlaybookHome } from "./Home";

/** Infra Playbook: rules and how-to guides for IT Support as branching mind maps. */
export const playbookModule: FrontendModule = {
  id: "playbook",
  home: PlaybookHome,
  routes: [
    { path: "playbook", element: <RequirePermission permission="playbook.guides.view"><GuidesPage /></RequirePermission> },
    { path: "playbook/for-step", element: <RequirePermission permission="playbook.guides.view"><StepGuidePage /></RequirePermission> },
    { path: "playbook/:id", element: <RequirePermission permission="playbook.guides.view"><GuidePage /></RequirePermission> },
    // Links saved before the rename (bookmarks, notes) keep working.
    { path: "handbook/*", element: <LegacyRedirect /> },
    { path: "handbook", element: <Navigate to="/playbook" replace /> },
    { path: "playbook/:id/edit", element: <RequirePermission permission="playbook.guides.edit"><GuideEditorPage /></RequirePermission> },
  ],
};

function LegacyRedirect() {
  const { pathname, search } = useLocation();
  return <Navigate to={pathname.replace(/^\/handbook/, "/playbook") + search} replace />;
}
