import { RequirePermission } from "@/core/auth/guards";
import type { FrontendModule } from "@/core/modules";
import { GuideEditorPage } from "./pages/GuideEditorPage";
import { GuidePage } from "./pages/GuidePage";
import { GuidesPage } from "./pages/GuidesPage";

/** Infra Playbook: rules and how-to guides for IT Support as branching mind maps. */
export const handbookModule: FrontendModule = {
  id: "handbook",
  routes: [
    { path: "handbook", element: <RequirePermission permission="handbook.guides.view"><GuidesPage /></RequirePermission> },
    { path: "handbook/:id", element: <RequirePermission permission="handbook.guides.view"><GuidePage /></RequirePermission> },
    { path: "handbook/:id/edit", element: <RequirePermission permission="handbook.guides.edit"><GuideEditorPage /></RequirePermission> },
  ],
};
