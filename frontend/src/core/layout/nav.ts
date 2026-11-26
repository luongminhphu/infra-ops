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

/** The entry module (its pages live under the Playbook rail entry, not under Công cụ). */
const ENTRY = "playbook";

/** Module pages by menu heading (the order the backend gives each heading); a page's own order is kept within one. */
const byHeading = (items: NavItem[]) => [...items].sort((a, b) => (a.group_order ?? 99) - (b.group_order ?? 99));

/**
 * Rail = Playbook (the entry point: "what do you need to handle?"), Việc của tôi, Công cụ (module pages grouped
 * by what they are for – opened from Playbook steps or from here), Báo cáo (statistics and review reports)
 * and Hệ thống at the bottom.
 */
export function railSections(nav: NavGroup[]): RailSection[] {
  const out: RailSection[] = [];
  let system: NavItem[] = [];
  let home: NavItem | undefined;
  let mine: NavItem | undefined;
  const entry: NavItem[] = [];
  const tools: NavItem[] = [];
  const reports: NavItem[] = [];
  for (const g of nav) {
    if (g.module === "core") {
      home = g.items.find((i) => i.path === "/");
      mine = g.items.find((i) => i.path === "/me/work");
      reports.push(...g.items.filter((i) => i.section === "reports"));
      system = g.items.filter((i) => i.path !== "/" && i.path !== "/me/work" && i.section !== "reports");
    } else if (g.module === ENTRY) {
      entry.push(...g.items);
    } else {
      for (const i of g.items) (i.section === "reports" ? reports : tools).push({ ...i, group: i.group || g.label });
    }
  }
  if (home) {
    const start = { ...home, label: "Tình huống", icon: "book-open" };
    out.push({ id: "home", label: "Playbook", title: "Infra Playbook", icon: "book-open", items: [start, ...entry] });
  }
  if (mine) out.push({ id: "mine", label: "Việc của tôi", title: "Việc của tôi", icon: mine.icon, items: [mine] });
  if (tools.length && home) {
    const all = { label: "Tất cả công cụ", path: "/tools", icon: "layout-grid", permission: home.permission };
    out.push({ id: "tools", label: "Công cụ", title: "Công cụ", icon: "layout-grid", items: [all, ...byHeading(tools)] });
  }
  if (reports.length) out.push({ id: "reports", label: "Báo cáo", title: "Báo cáo", icon: "file-chart-column", items: byHeading(reports) });
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
