import type { LucideIcon } from "lucide-react";
import {
  Box,
  Fingerprint,
  LayoutDashboard,
  Plug,
  ScrollText,
  ShieldCheck,
  Ticket,
  Users,
  FileText,
  GitCompareArrows,
  RefreshCw,
  Settings,
  Workflow,
} from "lucide-react";

/** Icon names used by backend manifests (nav items, connectors) → components. */
const ICONS: Record<string, LucideIcon> = {
  "layout-dashboard": LayoutDashboard,
  plug: Plug,
  "scroll-text": ScrollText,
  "shield-check": ShieldCheck,
  ticket: Ticket,
  fingerprint: Fingerprint,
  users: Users,
  "file-text": FileText,
  "git-compare-arrows": GitCompareArrows,
  workflow: Workflow,
  "refresh-cw": RefreshCw,
  settings: Settings,
};

export function iconFor(name: string): LucideIcon {
  return ICONS[name] ?? Box;
}
