import { render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import { ModulesContext } from "@/core/modules";

// Operator without lifecycle.job.assign: sees the wizard and the Playbook panel (playbook.guides.view only).
const h = vi.hoisted(() => ({ perms: new Set(["lifecycle.job.execute", "playbook.guides.view"]) }));
vi.mock("@/core/auth/AuthProvider", () => ({ useAuth: () => ({ can: (p: string) => h.perms.has(p), me: { nav: [] } }) }));

import { JobWizard } from "./JobWizard";
import type { Job } from "../jobs";

const job = {
  id: "job1", workflow_key: "lifecycle.onboard", workflow_title: "Onboard", subject_label: "An", subject_key: "003551", status: "running",
  current_step: "s1", params: {}, external_ref: "1", steps: [{ key: "s1", seq: 1, title: "Bước một", kind: "manual", status: "waiting", writes: [], checklist: [] }],
  progress: { done: 0, total: 1 }, assignee: null, assignments: [], events: [],
} as unknown as Job;

const Panel = ({ jobId, collapsible }: { jobId: string; collapsible?: boolean }) => <p>panel {jobId} {collapsible ? "collapsible" : "full"}</p>;
const wizard = (permission: string, j: Job = job) => render(
  <QueryClientProvider client={new QueryClient()}>
    <ModulesContext.Provider value={[{ id: "playbook", routes: [], jobPanels: [{ key: "playbook", order: 1, permission, title: "Playbook", component: Panel }] }]}>
      <MemoryRouter><JobWizard job={j} onFull={() => {}} /></MemoryRouter>
    </ModulesContext.Provider>
  </QueryClientProvider>,
);

describe("Job page panels", () => {
  it("wizard shows a module panel (collapsible) to a user without lifecycle.job.assign", () => {
    wizard("playbook.guides.view");
    expect(screen.getByText("panel job1 collapsible")).toBeTruthy();
  });
  it("hides the panel without its permission", () => {
    wizard("playbook.guides.edit");
    expect(screen.queryByText(/^panel/)).toBeNull();
  });
  it("shows the panel in the completed and scheduled empty states too", () => {
    wizard("playbook.guides.view", { ...job, status: "completed" } as Job);
    expect(screen.getByText("panel job1 collapsible")).toBeTruthy();
  });
  it("shows the panel for a scheduled job", () => {
    wizard("playbook.guides.view", { ...job, status: "scheduled" } as Job);
    expect(screen.getByText("panel job1 collapsible")).toBeTruthy();
  });
});
