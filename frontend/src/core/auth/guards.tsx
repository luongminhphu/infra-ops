import type { ReactNode } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { ShieldOff } from "lucide-react";
import { EmptyState, ErrorState, LoadingRows } from "@/core/ui/feedback";
import { useAuth } from "./AuthProvider";

export function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading, error, refresh } = useAuth();
  const location = useLocation();
  if (loading) return <LoadingRows rows={3} label="Đang kiểm tra phiên đăng nhập" />;
  if (error)
    return (
      <div className="p-6">
        <ErrorState error={error} onRetry={refresh} />
      </div>
    );
  if (!me) return <Navigate to={`/login?next=${encodeURIComponent(location.pathname + location.search)}`} replace />;
  return <>{children}</>;
}

export function RequirePermission({ permission, children }: { permission: string; children: ReactNode }) {
  const { can, me } = useAuth();
  if (can(permission)) return <>{children}</>;
  return (
    <EmptyState icon={ShieldOff} title="Bạn chưa có quyền xem trang này">
      {me?.permissions.length
        ? "Liên hệ quản trị viên infra-ops nếu bạn cần truy cập."
        : "Tài khoản chưa được gán vai trò. Liên hệ quản trị viên infra-ops để được cấp quyền."}
    </EmptyState>
  );
}
