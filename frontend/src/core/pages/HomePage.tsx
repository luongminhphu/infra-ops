import { useModules } from "@/core/modules";
import { DashboardPage } from "./DashboardPage";

/** Home route: the entry module's page (Playbook – "what do you need to handle?"), else the overview. */
export function HomePage() {
  const Home = useModules().find((m) => m.home)?.home;
  return Home ? <Home /> : <DashboardPage />;
}
