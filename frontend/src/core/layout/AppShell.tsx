import { useEffect, useMemo } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useAuth } from "@/core/auth/AuthProvider";
import { iconFor } from "@/core/ui/icons";
import { cn } from "@/core/lib/cn";
import { activeFor, railSections, type RailSection } from "./nav";
import { Topbar } from "./Topbar";

function RailLink({ s, active, tab = false }: { s: RailSection; active: boolean; tab?: boolean }) {
  const Icon = iconFor(s.icon);
  return (
    <li className={tab ? "flex-1" : undefined}>
      <Link
        to={s.items[0]!.path}
        aria-current={active ? "page" : undefined}
        title={s.title}
        className={cn(
          "flex flex-col items-center gap-0.5 rounded-sm px-1 py-1.5 text-center text-[11px] leading-tight font-medium transition-colors duration-150",
          tab ? "w-full" : "w-16",
          active ? "bg-primary-soft text-primary" : "text-muted hover:bg-surface-2 hover:text-ink",
        )}
      >
        <Icon className="size-5" aria-hidden="true" />
        {s.label}
      </Link>
    </li>
  );
}

/**
 * "Phòng điều khiển" shell: an icon rail with one entry per module (home, modules, then platform
 * settings at the bottom) and a sub-navigation for the pages of the active module.
 */
export function AppShell() {
  const { me } = useAuth();
  const location = useLocation();
  const sections = useMemo(() => railSections(me?.nav ?? []), [me?.nav]);
  const { section } = activeFor(sections, location.pathname);
  const sub = section && section.items.length > 1 ? section : null;

  // Move focus to the main heading after navigation so screen readers announce the new page.
  useEffect(() => {
    document.querySelector<HTMLElement>("main h1")?.focus({ preventScroll: true });
    // Phone sub-nav scrolls horizontally: keep the current page's tab in view.
    document
      .querySelector<HTMLElement>('[data-subnav-strip] [aria-current="page"]')
      ?.scrollIntoView({ block: "nearest", inline: "center" });
  }, [location.pathname]);

  const subLinks = (horizontal: boolean) =>
    sub!.items.map((item) => {
      const Icon = iconFor(item.icon);
      return (
        <li key={item.path} className={horizontal ? "shrink-0" : undefined}>
          <NavLink
            to={item.path}
            end={item.path === "/"}
            className={({ isActive }) =>
              cn(
                "flex h-8 items-center gap-2.5 rounded-sm px-2 text-base whitespace-nowrap transition-colors duration-150",
                isActive ? "bg-primary-soft font-medium text-primary" : "text-ink hover:bg-surface-2",
              )
            }
          >
            <Icon className="size-4 shrink-0" aria-hidden="true" />
            <span className={horizontal ? undefined : "truncate"}>{item.label}</span>
          </NavLink>
        </li>
      );
    });

  return (
    <div className="flex min-h-dvh">
      <a
        href="#main"
        className="sr-only z-50 rounded-sm bg-primary px-3 py-2 text-on-primary focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        Bỏ qua điều hướng
      </a>
      <nav aria-label="Phân hệ" className="sticky top-0 hidden h-dvh w-[var(--rail-w)] shrink-0 flex-col items-center border-r border-line bg-rail py-2 sm:flex">
        <Link to="/" className="mb-2 flex h-10 items-center rounded-sm px-2" aria-label="infra-ops – Tổng quan">
          <img src="/favicon.svg" alt="" className="size-7" />
        </Link>
        <ul className="flex flex-1 flex-col items-center gap-1 overflow-y-auto">
          {sections.filter((s) => !s.bottom).map((s) => <RailLink key={s.id} s={s} active={s.id === section?.id} />)}
        </ul>
        <ul className="flex flex-col items-center gap-1 pt-2">
          {sections.filter((s) => s.bottom).map((s) => <RailLink key={s.id} s={s} active={s.id === section?.id} />)}
        </ul>
      </nav>
      {sub && (
        <nav aria-label={sub.title} className="sticky top-0 hidden h-dvh w-[var(--subnav-w)] shrink-0 flex-col border-r border-line bg-surface lg:flex">
          <h2 className="flex h-[var(--topbar-h)] shrink-0 items-center border-b border-line px-4 text-base font-semibold">{sub.title}</h2>
          <ul className="flex-1 space-y-0.5 overflow-y-auto p-2">{subLinks(false)}</ul>
        </nav>
      )}
      <div className="flex min-w-0 flex-1 flex-col">
        <Topbar />
        {sub && (
          <nav aria-label={`${sub.title} (thu gọn)`} className="border-b border-line bg-surface lg:hidden">
            <ul data-subnav-strip className="flex gap-1 overflow-x-auto px-3 py-1.5">{subLinks(true)}</ul>
          </nav>
        )}
        <main id="main" className="mx-auto w-full max-w-[1600px] flex-1 px-4 pt-5 pb-24 sm:px-5 sm:pb-5 [&_h1]:outline-none" tabIndex={-1}>
          <Outlet />
        </main>
        {/* Phones: the module rail becomes a bottom tab bar so content gets the full width. */}
        <nav aria-label="Phân hệ" className="fixed inset-x-0 bottom-0 z-30 border-t border-line bg-rail pb-[env(safe-area-inset-bottom)] sm:hidden">
          <ul className="flex h-[var(--tabbar-h)] items-center gap-1 px-2">
            {sections.map((s) => <RailLink key={s.id} s={s} active={s.id === section?.id} tab />)}
          </ul>
        </nav>
      </div>
    </div>
  );
}
