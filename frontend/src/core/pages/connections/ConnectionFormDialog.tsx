import { useEffect, useState, type FormEvent } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api, ApiError, errorMessage } from "@/core/api/client";
import type { ConnectionProfile, ConnectorType, JsonSchemaProp } from "@/core/api/types";
import { Button } from "@/core/ui/button";
import { Dialog } from "@/core/ui/dialog";
import { Field, Input, Select, Switch, Textarea } from "@/core/ui/field";
import { announce } from "@/core/ui/live-region";
import { connectionsKey } from "./api";

type Config = Record<string, unknown>;

function defaults(type: ConnectorType | undefined): Config {
  const props = type?.config_schema.properties ?? {};
  return Object.fromEntries(Object.entries(props).map(([k, p]) => [k, p.default ?? (p.type === "boolean" ? false : "")]));
}

/** Renders a connector's non-secret config from its JSON schema (string, number, boolean, enum). */
function ConfigField({ name, prop, value, onChange }: { name: string; prop: JsonSchemaProp; value: unknown; onChange: (v: unknown) => void }) {
  const label = prop.title ?? name;
  if (prop.type === "boolean")
    return <Switch label={label} description={prop.description} checked={Boolean(value)} onChange={onChange} />;
  if (prop.enum)
    return (
      <Field label={label} hint={prop.description}>
        <Select value={String(value ?? "")} onChange={(e) => onChange(e.target.value)}>
          {prop.enum.map((o) => (
            <option key={o} value={o}>
              {ENUM_LABEL[o] ?? o}
            </option>
          ))}
        </Select>
      </Field>
    );
  return (
    <Field label={label} hint={prop.description}>
      <Input
        type={prop.type === "integer" || prop.type === "number" ? "number" : "text"}
        value={String(value ?? "")}
        onChange={(e) => onChange(prop.type === "integer" ? Number(e.target.value) : e.target.value)}
      />
    </Field>
  );
}

const ENUM_LABEL: Record<string, string> = { onprem: "On-prem (authtoken)", cloud: "Cloud (Zoho OAuth)" };

export function ConnectionFormDialog({
  open,
  onClose,
  types,
  profile,
}: {
  open: boolean;
  onClose: () => void;
  types: ConnectorType[];
  profile?: ConnectionProfile;
}) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const editing = Boolean(profile);
  const [systemType, setSystemType] = useState("");
  const [name, setName] = useState("");
  const [baseUrl, setBaseUrl] = useState("");
  const [environment, setEnvironment] = useState<"prod" | "test">("prod");
  const [enabled, setEnabled] = useState(true);
  const [config, setConfig] = useState<Config>({});
  const [notes, setNotes] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [serverError, setServerError] = useState("");

  useEffect(() => {
    if (!open) return;
    const t = profile?.system_type ?? types[0]?.system_type ?? "";
    setSystemType(t);
    setName(profile?.name ?? "");
    setBaseUrl(profile?.base_url ?? "");
    setEnvironment(profile?.environment ?? "prod");
    setEnabled(profile?.enabled ?? true);
    setConfig(profile ? { ...defaults(types.find((x) => x.system_type === t)), ...profile.config } : defaults(types.find((x) => x.system_type === t)));
    setNotes(profile?.notes ?? "");
    setSubmitted(false);
    setServerError("");
  }, [open, profile, types]);

  const type = types.find((t) => t.system_type === systemType);
  const urlError = !baseUrl
    ? "Nhập địa chỉ máy chủ."
    : !/^https?:\/\/\S+$/.test(baseUrl)
      ? "Địa chỉ phải bắt đầu bằng http:// hoặc https://"
      : undefined;
  const nameError = !name.trim() ? "Đặt tên cho kết nối." : undefined;

  const save = useMutation({
    mutationFn: () => {
      const body = { name: name.trim(), base_url: baseUrl.trim(), environment, enabled, config, notes };
      return profile
        ? api<ConnectionProfile>(`/api/core/connections/${profile.id}`, { method: "PATCH", json: { ...body, version: profile.version } })
        : api<ConnectionProfile>("/api/core/connections", { method: "POST", json: { ...body, system_type: systemType } });
    },
    onSuccess: async (saved) => {
      await qc.invalidateQueries({ queryKey: connectionsKey });
      if (saved.credentials_cleared)
        toast.warning("Đã lưu. Địa chỉ máy chủ đã đổi nên credential cũ đã bị xoá – hãy nhập lại credential.");
      else toast.success(editing ? "Đã lưu thay đổi kết nối." : "Đã thêm kết nối. Nhập API key (credential Read) để bắt đầu sử dụng.");
      onClose();
      // Step 2: the detail page opens the Read credential dialog straight away.
      if (!editing) navigate(`/settings/connections/${saved.id}?credential=read`);
    },
    onError: (e) => {
      const msg = e instanceof ApiError && e.code === "stale_version" ? e.message : errorMessage(e);
      setServerError(msg);
      announce(msg);
    },
  });

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setSubmitted(true);
    if (urlError || nameError) {
      announce("Vui lòng sửa các trường được đánh dấu.");
      return;
    }
    save.mutate();
  };

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={editing ? `Sửa kết nối ${profile?.name}` : "Thêm kết nối hệ thống"}
      description={editing ? undefined : "Bước 1/2: thông tin máy chủ. Bấm “Tiếp: nhập API key” để sang bước nhập credential (luôn được mã hoá)."}
      size="md"
    >
      <form onSubmit={submit} noValidate className="space-y-4">
        <Field label="Loại hệ thống" required hint={type?.description}>
          <Select
            value={systemType}
            disabled={editing}
            onChange={(e) => {
              setSystemType(e.target.value);
              setConfig(defaults(types.find((t) => t.system_type === e.target.value)));
            }}
          >
            {types.map((t) => (
              <option key={t.system_type} value={t.system_type}>
                {t.display_name}
              </option>
            ))}
          </Select>
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Tên kết nối" required error={nameError} showError={submitted}>
            <Input value={name} onChange={(e) => setName(e.target.value)} placeholder={type ? `${type.display_name} – Prod` : ""} />
          </Field>
          <Field label="Môi trường">
            <Select value={environment} onChange={(e) => setEnvironment(e.target.value as "prod" | "test")}>
              <option value="prod">Production</option>
              <option value="test">Test</option>
            </Select>
          </Field>
        </div>
        <Field label="Địa chỉ máy chủ (Base URL)" required error={urlError} showError={submitted} hint={type?.base_url_hint && `Ví dụ: ${type.base_url_hint}`}>
          <Input value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} inputMode="url" spellCheck={false} className="font-mono" />
        </Field>
        {type && Object.keys(type.config_schema.properties ?? {}).length > 0 && (
          <fieldset className="space-y-3 rounded-sm border border-line p-3">
            <legend className="px-1 text-sm font-semibold">Cấu hình {type.display_name}</legend>
            {Object.entries(type.config_schema.properties ?? {})
              .filter(([, prop]) => Object.entries(prop["x-show-when"] ?? {}).every(([k, v]) => config[k] === v))
              .map(([key, prop]) => (
              <ConfigField key={key} name={key} prop={prop} value={config[key]} onChange={(v) => setConfig((c) => ({ ...c, [key]: v }))} />
            ))}
          </fieldset>
        )}
        <Switch label="Bật kết nối" description="Khi tắt, các module không dùng kết nối này." checked={enabled} onChange={setEnabled} />
        <Field label="Ghi chú">
          <Textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={2} />
        </Field>
        {serverError && (
          <p role="alert" className="rounded-sm bg-failed-soft px-2.5 py-2 text-sm text-failed">
            {serverError}
          </p>
        )}
        <div className="flex justify-end gap-2 border-t border-line pt-3">
          <Button onClick={onClose}>Huỷ</Button>
          <Button type="submit" variant="primary" loading={save.isPending}>
            {editing ? "Lưu thay đổi" : "Tiếp: nhập API key"}
          </Button>
        </div>
      </form>
    </Dialog>
  );
}
