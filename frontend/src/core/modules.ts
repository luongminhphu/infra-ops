import type { RouteObject } from "react-router-dom";

/**
 * Frontend half of a module. Routes are mounted inside the app shell; navigation entries
 * come from the backend manifest (filtered by permission), so a module never edits core files
 * beyond being listed in src/modules/index.ts.
 */
export interface FrontendModule {
  id: string;
  routes: RouteObject[];
}
