import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQueryClient } from "@tanstack/react-query";
import { KeyRound, LogOut, Monitor, Moon, Search, Sun, UserRound } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import { useAuth } from "@/core/auth/AuthProvider";
import { getTheme, setTheme, type ThemeChoice } from "@/core/lib/theme";
import { Button } from "@/core/ui/button";
import { Kbd } from "@/core/ui/feedback";
import { Menu, MenuItem, MenuLabel, MenuSeparator } from "@/core/ui/menu";
import { CommandPalette } from "./CommandPalette";
import { ChangePasswordDialog } from "./ChangePasswordDialog";

const THEMES: { key: ThemeChoice; label: string; icon: typeof Sun }[] = [
  { key: "system", label: "Theo hệ thống", icon: Monitor },
  { key: "light", label: "Sáng", icon: Sun },
  { key: "dark", label: "Tối", icon: Moon },
];

const ROLE_LABEL: Record<string, string> = { admin: "Quản trị", manager: "Manager", supervisor: "Supervisor", operator: "Operator", viewer: "Chỉ xem" };

export function Topbar() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [theme, setThemeState] = useState<ThemeChoice>(getTheme);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [pwOpen, setPwOpen] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const chooseTheme = (t: ThemeChoice) => {
    setTheme(t);
    setThemeState(t);
  };

  const logout = async () => {
    try {
      await api("/api/core/auth/logout", { method: "POST" });
    } catch (e) {
      toast.error(errorMessage(e));
    }
    qc.clear();
    navigate("/login", { replace: true });
  };

  const ThemeIcon = THEMES.find((t) => t.key === theme)?.icon ?? Monitor;

  return (
    <header className="sticky top-0 z-30 flex h-[var(--topbar-h)] items-center gap-3 border-b border-line bg-surface/95 px-5 backdrop-blur">
      <button
        type="button"
        onClick={() => setPaletteOpen(true)}
        className="flex h-8 w-full max-w-sm cursor-pointer items-center gap-2 rounded-sm border border-line bg-paper px-2.5 text-base text-muted hover:border-line-strong"
      >
        <Search className="size-4" aria-hidden="true" />
        <span className="flex-1 text-left">Tìm trang, chức năng…</span>
        <Kbd>Ctrl K</Kbd>
      </button>
      <div className="ml-auto flex items-center gap-1">
        <Menu
          label="Giao diện"
          trigger={(p) => (
            <Button variant="ghost" size="icon" aria-label={`Giao diện: ${THEMES.find((t) => t.key === theme)?.label}`} {...p}>
              <ThemeIcon className="size-4" aria-hidden="true" />
            </Button>
          )}
        >
          <MenuLabel>Giao diện</MenuLabel>
          {THEMES.map((t) => (
            <MenuItem key={t.key} checked={theme === t.key} onSelect={() => chooseTheme(t.key)}>
              <t.icon className="size-4" aria-hidden="true" />
              <span className="flex-1">{t.label}</span>
              {theme === t.key && <span className="text-xs text-primary">Đang dùng</span>}
            </MenuItem>
          ))}
        </Menu>
        <Menu
          label="Tài khoản"
          trigger={(p) => (
            <Button variant="ghost" className="gap-2 px-2" {...p}>
              <span className="flex size-6 items-center justify-center rounded-full bg-primary-soft text-primary">
                <UserRound className="size-3.5" aria-hidden="true" />
              </span>
              <span className="max-w-40 truncate">{me?.display_name}</span>
            </Button>
          )}
        >
          <div className="px-2 py-1.5">
            <p className="font-medium">{me?.display_name}</p>
            <p className="truncate text-sm text-muted">{me?.upn}</p>
            <p className="mt-1 text-xs text-muted">
              Vai trò: {me?.roles.map((r) => ROLE_LABEL[r] ?? r).join(", ") || "chưa được gán"}
            </p>
          </div>
          <MenuSeparator />
          {me?.is_local && (
            <MenuItem onSelect={() => setPwOpen(true)}>
              <KeyRound className="size-4" aria-hidden="true" />
              Đổi mật khẩu
            </MenuItem>
          )}
          <MenuItem onSelect={logout}>
            <LogOut className="size-4" aria-hidden="true" />
            Đăng xuất
          </MenuItem>
        </Menu>
      </div>
      <CommandPalette open={paletteOpen} onClose={() => setPaletteOpen(false)} />
      <ChangePasswordDialog open={pwOpen} onClose={() => setPwOpen(false)} />
    </header>
  );
}
