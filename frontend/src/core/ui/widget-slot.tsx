import type { ComponentType } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/core/api/client";
import type { DashboardWidget } from "@/core/api/types";
import { useModules } from "@/core/modules";
import { ErrorState, Panel } from "@/core/ui/feedback";

/** Finds the renderer a module registered for a dashboard widget `kind`. */
export function useWidgetRenderer() {
  const modules = useModules();
  return (kind: string) =>
    modules.map((m) => m.widgets?.[kind]).find(Boolean) as ComponentType<{ data: unknown }> | undefined;
}

/** One dashboard widget by kind, outside the overview page (e.g. the team queue on the Playbook home). */
export function WidgetSlot({ kind }: { kind: string }) {
  const q = useQuery({ queryKey: ["dashboard"], queryFn: () => api<DashboardWidget[]>("/api/core/dashboard") });
  const Widget = useWidgetRenderer()(kind);
  const w = q.data?.find((x) => x.kind === kind);
  if (!w || !Widget) return null;
  return (
    <Panel title={w.title} bodyClassName="py-1">
      {w.error ? <ErrorState error={new Error(w.error)} /> : <Widget data={w.data} />}
    </Panel>
  );
}
