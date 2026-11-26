import { Link } from "react-router-dom";
import { ArrowUpRight } from "lucide-react";
import { useAuth } from "@/core/auth/AuthProvider";
import type { NavItem } from "@/core/api/types";
import { iconFor } from "@/core/ui/icons";
import { toolKey, type StepNode } from "./api";

/** The app pages this person may open, by tool key (the same list as Công cụ, filtered by permission). */
export function useTools(): Map<string, NavItem & { module: string }> {
  const { me } = useAuth();
  const out = new Map<string, NavItem & { module: string }>();
  for (const g of me?.nav ?? []) {
    if (g.module === "core" || g.module === "playbook") continue;
    for (const i of g.items) out.set(toolKey(i.path), { ...i, module: g.label });
  }
  return out;
}

/** The step's tools as buttons; a tool this person may not open is not shown. */
export function StepActions({ node }: { node: StepNode }) {
  const tools = useTools();
  const items = (node.actions ?? []).map((a) => ({ a, t: tools.get(a.tool) })).filter((x) => x.t);
  if (!items.length) return null;
  return (
    <div className="mt-4 flex flex-wrap gap-2">
      {items.map(({ a, t }) => {
        const Icon = iconFor(t!.icon);
        return (
          <Link key={a.tool} to={t!.path} className="inline-flex min-h-9 items-center gap-2 rounded-sm border border-line-strong bg-surface px-3 text-sm font-medium hover:border-primary hover:text-primary">
            <Icon className="size-4" aria-hidden="true" />
            {a.label || t!.label}
            <ArrowUpRight className="size-3.5 text-muted" aria-hidden="true" />
          </Link>
        );
      })}
    </div>
  );
}
