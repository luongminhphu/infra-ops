import { useState, type FormEvent } from "react";
import { Navigate, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { LogIn } from "lucide-react";
import { api, errorMessage } from "@/core/api/client";
import { Button } from "@/core/ui/button";
import { Field, Input } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";
import { ME_KEY, useAuth } from "./AuthProvider";

export function LoginPage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const next = params.get("next") ?? "/";
  const config = useQuery({ queryKey: ["auth-config"], queryFn: () => api<{ mode: string }>("/api/core/auth/config") });
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState(params.get("error") === "sso" ? "Đăng nhập Microsoft không thành công. Thử lại." : "");
  const [busy, setBusy] = useState(false);

  if (me) return <Navigate to={next} replace />;
  const mode = config.data?.mode;

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (!username || (mode === "local" && !password)) return;
    setBusy(true);
    setError("");
    try {
      await api("/api/core/auth/login", { method: "POST", json: { username, password } });
      await qc.invalidateQueries({ queryKey: ME_KEY });
      navigate(next.startsWith("/") && !next.startsWith("//") ? next : "/", { replace: true });
    } catch (err) {
      setError(errorMessage(err));
      announce(errorMessage(err));
    } finally {
      setBusy(false);
    }
  };

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
          {mode === "oidc" ? (
            <a
              href={`/api/core/auth/oidc/login?next=${encodeURIComponent(next)}`}
              className="flex h-9 w-full items-center justify-center gap-2 rounded-sm bg-primary font-medium text-on-primary hover:bg-primary-hover"
            >
              <LogIn className="size-4" aria-hidden="true" />
              Đăng nhập bằng tài khoản Microsoft
            </a>
          ) : (
            <form onSubmit={submit} noValidate className="space-y-3">
              <Field
                label={mode === "dev" ? "UPN (chế độ phát triển)" : "Tên đăng nhập"}
                required
                error={!username ? "Nhập tên đăng nhập." : undefined}
                showError={submitted}
              >
                <Input autoComplete="username" autoFocus value={username} onChange={(e) => setUsername(e.target.value)} />
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
          )}
          {mode === "dev" && (
            <p className="mt-3 text-xs text-muted">Chế độ phát triển: không cần mật khẩu. Không dùng ở môi trường thật.</p>
          )}
        </div>
      </div>
    </main>
  );
}
