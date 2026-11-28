import { createContext, useContext, type ComponentType } from "react";
import type { RouteObject } from "react-router-dom";

/**
 * Frontend half of a module. Routes are mounted inside the app shell; navigation entries
 * come from the backend manifest (filtered by permission), so a module never edits core files
 * beyond being listed in src/modules/index.ts.
 */
export interface FrontendModule {
  id: string;
  routes: RouteObject[];
  /** Renderers for dashboard widgets this module declares in its backend manifest, keyed by `kind`. */
  widgets?: Record<string, ComponentType<{ data: unknown }>>;
  /** Sections of the "Việc của tôi" portal (each fetches the signed-in user's own work). */
  portal?: { key: string; order: number; permission: string; component: ComponentType }[];
  /** Panels shown on the Job page (wizard: collapsible below the current step; full view: a section). A panel renders nothing when it has nothing to say. `version` changes every time the Job changes (use it as a refetch key); `collapsible` is true in the wizard. */
  jobPanels?: { key: string; order: number; permission: string; title: string; component: ComponentType<{ jobId: string; workflowKey: string; status: string; version: number; collapsible?: boolean }> }[];
  /** The app's home page ("/"), when this module is the entry point (Playbook). */
  home?: ComponentType;
}

/** The registered modules, provided once by the app (src/app/App.tsx) so core never imports src/modules. */
export const ModulesContext = createContext<FrontendModule[]>([]);
export const useModules = () => useContext(ModulesContext);
