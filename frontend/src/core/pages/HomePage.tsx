import { modules } from "@/modules";
import { DashboardPage } from "./DashboardPage";

/** Home route: the entry module's page (Playbook – "what do you need to handle?"), else the overview. */
export function HomePage() {
  // Read on render, not at import (the module list and the Playbook home import each other through core pages).
  const Home = modules.find((m) => m.home)?.home;
  return Home ? <Home /> : <DashboardPage />;
}
