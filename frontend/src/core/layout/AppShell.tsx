import { useEffect, useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { PanelLeftClose, PanelLeftOpen } from "lucide-react";
import { useAuth } from "@/core/auth/AuthProvider";
import { iconFor } from "@/core/ui/icons";
import { cn } from "@/core/lib/cn";
import { Topbar } from "./Topbar";

const COLLAPSE_KEY = "sidebar-collapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

export function AppShell() {
  const { me } = useAuth();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const location = useLocation();

  useEffect(() => {
    try {
      localStorage.setItem(COLLAPSE_KEY, collapsed ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [collapsed]);

  // Move focus to the main heading after navigation so screen readers announce the new page.
  useEffect(() => {
    document.querySelector<HTMLElement>("main h1")?.focus({ preventScroll: true });
  }, [location.pathname]);

  return (
    <div className="flex min-h-dvh">
      <a
        href="#main"
        className="sr-only z-50 rounded-sm bg-primary px-3 py-2 text-on-primary focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Bỏ qua điều hướng
      </a>
      <aside
        className={cn(
          "sticky top-0 flex h-dvh shrink-0 flex-col border-r border-line bg-surface transition-[width] duration-200",
          collapsed ? "w-[var(--sidebar-w-collapsed)]" : "w-[var(--sidebar-w)]",
        )}
      >
        <div className="flex h-[var(--topbar-h)] items-center gap-2 border-b border-line px-3">
          <img src="/favicon.svg" alt="" className="size-6 shrink-0" />
          {!collapsed && <span className="text-lg font-semibold tracking-tight">infra-ops</span>}
        </div>
        <nav aria-label="Điều hướng chính" className="flex-1 overflow-y-auto px-2 py-3">
          {me?.nav.map((group) => (
            <div key={group.module} className="mb-4">
              {!collapsed && <h2 className="px-2 pb-1 text-xs font-medium text-muted">{group.label}</h2>}
              <ul className="space-y-0.5">
                {group.items.map((item) => {
                  const Icon = iconFor(item.icon);
                  return (
                    <li key={item.path}>
                      <NavLink
                        to={item.path}
                        end={item.path === "/"}
                        title={collapsed ? item.label : undefined}
                        className={({ isActive }) =>
                          cn(
                            "flex h-8 items-center gap-2.5 rounded-sm px-2 text-base transition-colors duration-150",
                            isActive
                              ? "bg-primary-soft font-medium text-primary"
                              : "text-ink hover:bg-surface-2",
                          )
                        }
                      >
                        <Icon className="size-4 shrink-0" aria-hidden="true" />
                        <span className={cn("truncate", collapsed && "sr-only")}>{item.label}</span>
                      </NavLink>
                    </li>
                  );
                })}
              </ul>
            </div>
          ))}
        </nav>
        <button
          type="button"
          onClick={() => setCollapsed((v) => !v)}
          aria-label={collapsed ? "Mở rộng thanh bên" : "Thu gọn thanh bên"}
          className="flex h-10 cursor-pointer items-center gap-2 border-t border-line px-4 text-sm text-muted hover:text-ink"
        >
          {collapsed ? <PanelLeftOpen className="size-4" aria-hidden="true" /> : <PanelLeftClose className="size-4" aria-hidden="true" />}
          {!collapsed && <span>Thu gọn</span>}
        </button>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        <main id="main" className="mx-auto w-full max-w-[1600px] flex-1 px-5 py-5 [&_h1]:outline-none" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
