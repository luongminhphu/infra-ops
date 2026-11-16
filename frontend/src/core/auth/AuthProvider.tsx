import { createContext, useContext, useEffect, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError, setCsrfToken } from "@/core/api/client";
import type { Me } from "@/core/api/types";

interface AuthState {
  me: Me | null;
  loading: boolean;
  error: unknown;
  can: (permission: string) => boolean;
  refresh: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export const ME_KEY = ["me"] as const;

export function AuthProvider({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const query = useQuery({
    queryKey: ME_KEY,
    queryFn: async () => {
      try {
        return await api<Me>("/api/core/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
    retry: false,
  });

  useEffect(() => {
    if (query.data) setCsrfToken(query.data.csrf_token);
  }, [query.data]);

  const me = query.data ?? null;
  const value: AuthState = {
    me,
    loading: query.isLoading,
    error: query.error,
    can: (p) => !!me?.permissions.includes(p),
    refresh: async () => {
      await qc.invalidateQueries({ queryKey: ME_KEY });
    },
  };
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside AuthProvider");
  return ctx;
}
