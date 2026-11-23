import type { ComponentType } from "react";
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
}
