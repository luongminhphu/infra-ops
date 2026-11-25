import { useEffect, useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LogIn, ShieldAlert } from "lucide-react";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { Field, Input } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";
import { ME_KEY, useAuth } from "./AuthProvider";
import { resetLanding } from "./landing";

export function LoginPage() {
  // Signed out or session expired: the next sign-in lands on the role's home again (Operators: Việc của tôi).
  useEffect(resetLanding, []);
  const { me } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") ?? "/";
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api<{ mode: string; cookie_secure: boolean; local_login: boolean }>("/api/core/auth/config") });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState(params.get("error") === "sso" ? "Đăng nhập Microsoft không thành công. Thử lại hoặc liên hệ Quản trị viên nếu lỗi lặp lại." : "");
  const [busy, setBusy] = useState(false);

  if (me) return <Navigate to={next} replace />;
  const mode = config.data?.mode;
  // The session cookie is Secure: over plain http:// the browser drops it and every login "fails" silently.
  const needsHttps = !!config.data?.cookie_secure && window.location.protocol === "http:" && !["localhost", "127.0.0.1"].includes(window.location.hostname);
  const httpsUrl = `https://${window.location.hostname}${window.location.pathname}${window.location.search}`;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (!username || (mode !== "dev" && !password)) return;
    setBusy(true);
    setError("");
    try {
      await api("/api/core/auth/login", { method: "POST", json: { username, password } });
      try {
        await api("/api/core/me");
      } catch {
        throw new Error("Mật khẩu đúng nhưng trình duyệt không lưu phiên đăng nhập (cookie). Mở trang bằng địa chỉ https:// rồi đăng nhập lại.");
      }
      await qc.invalidateQueries({ queryKey: ME_KEY });
      navigate(next.startsWith("/") && !next.startsWith("//") ? next : "/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
      announce(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

  const loginForm = (
    <form onSubmit={submit} noValidate className="space-y-3">
      <Field
        label={mode === "dev" ? "Email công ty (chế độ phát triển)" : "Tên đăng nhập"}
        required
        error={!username ? "Nhập tên đăng nhập." : undefined}
        showError={submitted}
      >
        <Input autoComplete="username" autoFocus={mode !== "oidc"} value={username} onChange={(e) => setUsername(e.target.value)} />
      </Field>
      {mode !== "dev" && (
        <Field label="Mật khẩu" required error={!password ? "Nhập mật khẩu." : undefined} showError={submitted}>
          <Input type="password" autoComplete="current-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </Field>
      )}
      {error && (
        <p role="alert" className="rounded-sm bg-failed-soft px-2.5 py-2 text-sm text-failed">
          {error}
        </p>
      )}
      <Button type="submit" variant="primary" className="h-9 w-full" loading={busy} disabled={!mode}>
        Đăng nhập
      </Button>
    </form>
  );

  return (
    <main className="grid min-h-dvh place-items-center bg-paper px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2.5">
          <img src="/favicon.svg" alt="" className="size-8" />
          <div>
            <h1 className="text-xl font-semibold leading-tight">infra-ops</h1>
            <p className="text-sm text-muted">Vận hành hạ tầng CNTT</p>
          </div>
        </div>
        <div className="rounded-md border border-line bg-surface p-5">
          <h2 className="mb-4 text-lg font-semibold">Đăng nhập</h2>
          {needsHttps && (
            <div role="alert" className="mb-4 flex gap-2 rounded-sm border border-waiting/40 bg-waiting-soft px-3 py-2 text-sm">
              <ShieldAlert className="mt-0.5 size-4 shrink-0 text-waiting" aria-hidden="true" />
              <span>
                <span className="block font-medium">Cần mở trang bằng HTTPS để đăng nhập.</span>
                <span className="block">
                  Trình duyệt không lưu phiên đăng nhập trên địa chỉ http://. Mở{" "}
                  <a className="font-medium text-primary underline" href={httpsUrl}>{httpsUrl}</a> (cần cấu hình HTTPS trên máy chủ).
                </span>
                <span className="block text-muted">Quản trị: xem mục HTTPS trong hướng dẫn cài đặt; chỉ khi thử nghiệm mới đặt INFRAOPS_SESSION_COOKIE_SECURE=false.</span>
              </span>
            </div>
          )}
          {mode === "oidc" ? (
            <>
              {error && !submitted && (
                <p role="alert" className="mb-3 rounded-sm bg-failed-soft px-2.5 py-2 text-sm text-failed">
                  {error}
                </p>
              )}
            <a
              href={`/api/core/auth/oidc/login?next=${encodeURIComponent(next)}`}
              className="flex h-9 w-full items-center justify-center gap-2 rounded-sm bg-primary font-medium text-on-primary hover:bg-primary-hover"
            >
              <LogIn className="size-4" aria-hidden="true" />
              Đăng nhập bằng tài khoản Microsoft
            </a>
              {config.data?.local_login && (
                <details className="mt-4 border-t border-line pt-3" open={!!error && submitted}>
                  <summary className="cursor-pointer text-sm font-medium text-primary">Đăng nhập bằng tài khoản nội bộ</summary>
                  <p className="mt-1 mb-3 text-xs text-muted">Chỉ dành cho tài khoản được Administrator cấp mật khẩu trong infra-ops (vd. tài khoản dự phòng).</p>
              {loginForm}
                </details>
              )}
            </>
          ) : (
            loginForm
          )}
          {mode === "dev" && (
            <p className="mt-3 text-xs text-muted">Chế độ phát triển: không cần mật khẩu. Không dùng ở môi trường thật.</p>
          )}
        </div>
      </div>
    </main>
  );
}
