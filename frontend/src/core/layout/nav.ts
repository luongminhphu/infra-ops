import type { NavGroup, NavItem } from "@/core/api/types";

export interface RailSection {
  id: string;
  /** Short label under the rail icon. */
  label: string;
  /** Full name, shown as the sub-navigation heading. */
  title: string;
  icon: string;
  items: NavItem[];
  bottom?: boolean;
}

const SHORT: Record<string, string> = { lifecycle: "Vòng đời", workload: "Công việc", playbook: "Playbook" };
const ICON: Record<string, string> = { lifecycle: "users", workload: "list-checks", playbook: "book-open" };

/** Rail = Tổng quan, one entry per module, and the platform settings ("Hệ thống") at the bottom. */
export function railSections(nav: NavGroup[]): RailSection[] {
  const out: RailSection[] = [];
  let system: NavItem[] = [];
  for (const g of nav) {
    if (g.module === "core") {
      const home = g.items.find((i) => i.path === "/");
      const mine = g.items.find((i) => i.path === "/me/work");
      if (mine) out.unshift({ id: "mine", label: "Việc của tôi", title: "Việc của tôi", icon: mine.icon, items: [mine] });
      if (home) out.unshift({ id: "home", label: "Tổng quan", title: "Tổng quan", icon: home.icon, items: [home] });
      system = g.items.filter((i) => i.path !== "/" && i.path !== "/me/work");
    } else if (g.items.length) {
      out.push({ id: g.module, label: SHORT[g.module] ?? g.label, title: g.label, icon: ICON[g.module] ?? g.items[0]!.icon, items: g.items });
    }
  }
  if (system.length) out.push({ id: "system", label: "Hệ thống", title: "Hệ thống", icon: "settings", items: system, bottom: true });
  return out;
}

const matches = (item: NavItem, path: string) => (item.path === "/" ? path === "/" : path === item.path || path.startsWith(`${item.path}/`));

/** Section and item for a URL: the longest matching item path wins (e.g. /lifecycle/jobs/123). */
export function activeFor(sections: RailSection[], path: string): { section: RailSection | null; item: NavItem | null } {
  let best: { section: RailSection; item: NavItem } | null = null;
  for (const s of sections)
    for (const i of s.items) if (matches(i, path) && (!best || i.path.length > best.item.path.length)) best = { section: s, item: i };
  if (best) return best;
  // Pages without their own nav entry (e.g. /lifecycle/jobs/:id) belong to their module's section.
  const seg = path.split("/")[1];
  const section = sections.find((s) => s.items.some((i) => i.path.split("/")[1] === seg && seg)) ?? null;
  return { section, item: null };
}
