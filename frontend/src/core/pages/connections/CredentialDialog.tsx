import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { LockKeyhole } from "lucide-react";
import { toast } from "sonner";
import { api, errorMessage } from "@/core/api/client";
import type { ConnectionProfile, ConnectorType, Purpose } from "@/core/api/types";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input, Select, Textarea } from "@/core/ui/field";
import { DateInput } from "@/core/ui/date-input";
import { announce } from "@/core/ui/live-region";
import { connectionsKey } from "./api";

const PURPOSE_TEXT: Record<Purpose, string> = {
  read: "Dùng cho đối soát, báo cáo. Nên là tài khoản chỉ có quyền đọc.",
  write: "Dùng cho workflow (gán tài sản, tạo/vô hiệu hoá người dùng…). Không có credential Write thì thao tác ghi bị khoá.",
};

export function CredentialDialog({
  open,
  onClose,
  profile,
  type,
  purpose,
}: {
  open: boolean;
  onClose: () => void;
  profile: ConnectionProfile;
  type: ConnectorType;
  purpose: Purpose;
}) {
  const qc = useQueryClient();
  const existing = profile.credentials[purpose];
  const [authType, setAuthType] = useState("");
  const [values, setValues] = useState<Record<string, string>>({});
  const [expires, setExpires] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [serverError, setServerError] = useState("");

  useEffect(() => {
    if (!open) return;
    setAuthType(existing?.auth_type ?? type.auth_types[0]?.key ?? "");
    setValues({ ...(existing?.public ?? {}) });
    setExpires(existing?.expires_at ? existing.expires_at.slice(0, 10) : "");
    setSubmitted(false);
    setServerError("");
  }, [open, existing, type]);

  const spec = type.auth_types.find((a) => a.key === authType);
  const keepExisting = Boolean(existing && existing.auth_type === authType);
  const missing = (spec?.fields ?? []).filter((f) => f.required && !values[f.name] && !(f.secret && keepExisting));

  const save = useMutation({
    mutationFn: () => {
      const pub: Record<string, string> = {};
      const sec: Record<string, string> = {};
      for (const f of spec?.fields ?? []) (f.secret ? sec : pub)[f.name] = values[f.name] ?? "";
      return api<ConnectionProfile>(`/api/core/connections/${profile.id}/credentials/${purpose}`, {
        method: "PUT",
        json: { auth_type: authType, public: pub, secrets: sec, expires_at: expires ? `${expires}T00:00:00+07:00` : null },
      });
    },
    onSuccess: async () => {
      await qc.invalidateQueries({ queryKey: connectionsKey });
      toast.success(`Đã lưu credential ${purpose === "read" ? "Read" : "Write"}. Bấm “Kiểm tra kết nối” để xác nhận.`);
      onClose();
    },
    onError: (e) => {
      setServerError(errorMessage(e));
      announce(errorMessage(e));
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (missing.length) {
      announce("Vui lòng nhập đủ các trường bắt buộc.");
      return;
    }
    save.mutate();
  };

  const title = `${existing ? "Thay mới" : "Nhập"} credential ${purpose === "read" ? "Read" : "Write"}`;
  return (
    <Dialog open={open} onClose={onClose} title={title} description={PURPOSE_TEXT[purpose]} size="md">
      <form onSubmit={submit} noValidate className="space-y-4">
        {type.auth_types.length > 1 && (
          <Field label="Phương thức xác thực">
            <Select value={authType} onChange={(e) => setAuthType(e.target.value)}>
              {type.auth_types.map((a) => (
                <option key={a.key} value={a.key}>
                  {a.label}
                </option>
              ))}
            </Select>
          </Field>
        )}
        {spec?.fields.map((f) => {
          const keep = f.secret && keepExisting;
          const control = f.multiline ? (
            <Textarea
              value={values[f.name] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
              rows={5}
              className="font-mono text-sm"
              spellCheck={false}
              autoComplete="off"
              placeholder={keep ? "Để trống để giữ giá trị hiện tại" : undefined}
            />
          ) : (
            <Input
              type={f.secret ? "password" : "text"}
              value={values[f.name] ?? ""}
              onChange={(e) => setValues((v) => ({ ...v, [f.name]: e.target.value }))}
              autoComplete={f.secret ? "new-password" : "off"}
              spellCheck={false}
              placeholder={keep ? `Để trống để giữ giá trị hiện tại${existing?.hint ? ` (••••${existing.hint})` : ""}` : undefined}
            />
          );
          return (
            <Field
              key={`${authType}-${f.name}`}
              label={f.label}
              required={f.required && !keep}
              hint={f.help || undefined}
              error={missing.some((m) => m.name === f.name) ? `Nhập ${f.label.toLowerCase()}.` : undefined}
              showError={submitted}
            >
              {control}
            </Field>
          );
        })}
        <Field
          label="Ngày hết hạn"
          hint={authType === "certificate" ? "Để trống để tự đọc từ chứng chỉ." : "Dashboard sẽ cảnh báo trước khi secret hết hạn."}
        >
          <DateInput value={expires} onChange={setExpires} />
        </Field>
        <p className="flex items-start gap-2 rounded-sm bg-surface-2 px-2.5 py-2 text-sm text-muted">
          <LockKeyhole className="mt-0.5 size-4 shrink-0" aria-hidden="true" />
          Secret được mã hoá AES-256 khi lưu, không bao giờ hiển thị lại và không ghi vào nhật ký.
        </p>
        {serverError && (
          <p role="alert" className="rounded-sm bg-failed-soft px-2.5 py-2 text-sm text-failed">
            {serverError}
          </p>
        )}
        <div className="flex justify-end gap-2 border-t border-line pt-3">
          <Button onClick={onClose}>Huỷ</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>
            Lưu credential
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
