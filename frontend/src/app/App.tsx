import { MyWorkPage } from "@/core/pages/MyWorkPage";
import { MyCredentialsPage } from "@/core/pages/MyCredentialsPage";
import { createBrowserRouter, RouterProvider, type RouteObject } from "react-router-dom";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster, toast } from "sonner";
import { ApiError, errorMessage } from "@/core/api/client";
import { AuthProvider, ME_KEY } from "@/core/auth/AuthProvider";
import { LoginPage } from "@/core/auth/LoginPage";
import { RequireAuth, RequirePermission } from "@/core/auth/guards";
import { AppShell } from "@/core/layout/AppShell";
import { LiveRegion } from "@/core/ui/live-region";
import { HomePage } from "@/core/pages/HomePage";
import { ConnectionsPage } from "@/core/pages/connections/ConnectionsPage";
import { ConnectionDetailPage } from "@/core/pages/connections/ConnectionDetailPage";
import { AuditPage } from "@/core/pages/AuditPage";
import { AccessPage } from "@/core/pages/access/AccessPage";
import { NotFoundPage } from "@/core/pages/NotFoundPage";
import { modules } from "@/modules";

// Session expired (401 anywhere): re-check /me, which turns into null so RequireAuth redirects to /login.
// Only while signed in: a wrong password on the login page must not trigger a refetch.
function onApiError(error: unknown) {
  if (error instanceof ApiError && error.status === 401 && queryClient.getQueryData(ME_KEY))
    // exact: ["me","next"] etc. also start with "me"; re-fetching them would 401 again and loop.
    void queryClient.invalidateQueries({ queryKey: ME_KEY, exact: true });
}

const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({ onError: onApiError }),
  mutationCache: new MutationCache({
    onError: (error, _vars, _ctx, mutation) => {
      onApiError(error);
      // Default error toast; mutations with their own onError or meta.silentError handle errors themselves.
      if (!mutation.options.onError && !mutation.meta?.silentError) toast.error(errorMessage(error));
    },
  }),
  defaultOptions: {
    queries: {
      retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
      refetchOnWindowFocus: false,
    },
  },
});

const coreRoutes: RouteObject[] = [
  { index: true, element: <RequirePermission permission="core.dashboard.view"><HomePage /></RequirePermission> },
  { path: "me/work", element: <RequirePermission permission="core.dashboard.view"><MyWorkPage /></RequirePermission> },
  { path: "me/credentials", element: <MyCredentialsPage /> },
  { path: "settings/connections", element: <RequirePermission permission="core.connections.view"><ConnectionsPage /></RequirePermission> },
  { path: "settings/connections/:id", element: <RequirePermission permission="core.connections.view"><ConnectionDetailPage /></RequirePermission> },
  { path: "settings/audit", element: <RequirePermission permission="core.audit.view"><AuditPage /></RequirePermission> },
  { path: "settings/access", element: <RequirePermission permission="core.rbac.manage"><AccessPage /></RequirePermission> },
];

const router = createBrowserRouter([
  { path: "/login", element: <LoginPage /> },
  {
    path: "/",
    element: (
      <RequireAuth>
        <AppShell />
      </RequireAuth>
    ),
    children: [...coreRoutes, ...modules.flatMap((m) => m.routes), { path: "*", element: <NotFoundPage /> }],
  },
]);

export function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <RouterProvider router={router} />
        <LiveRegion />
        <Toaster
          position="bottom-right"
          closeButton
          toastOptions={{
            classNames: {
              toast: "!bg-surface !text-ink !border-line !font-sans",
              description: "!text-muted",
            },
          }}
        />
      </AuthProvider>
    </QueryClientProvider>
  );
}
