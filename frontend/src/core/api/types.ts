export interface NavItem {
  label: string;
  path: string;
  icon: string;
  permission: string;
  /** Menu heading the item is listed under (shared across modules; "" = under the module name). */
  group?: string;
  /** Position of that heading in the menu (set by the backend). */
  group_order?: number;
  /** Rail entry the page belongs to: "Công cụ" or "Báo cáo". */
  section?: "tools" | "reports";
}

export interface NavGroup {
  module: string;
  label: string;
  items: NavItem[];
}

export interface Me {
  id: string;
  upn: string;
  display_name: string;
  roles: string[];
  permissions: string[];
  csrf_token: string;
  auth_mode: "local" | "oidc" | "dev";
  /** Username/password sign-in accepted (local mode, or SSO with local accounts allowed). */
  local_login: boolean;
  is_local: boolean;
  nav: NavGroup[];
}

export interface AuthField {
  name: string;
  label: string;
  secret: boolean;
  required: boolean;
  multiline: boolean;
  help: string;
}

export interface AuthTypeDef {
  key: string;
  label: string;
  fields: AuthField[];
}

export interface JsonSchemaProp {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: string[];
  /** Show the field only when these other config values match (e.g. Cloud-only settings). */
  "x-show-when"?: Record<string, string>;
}

export interface ConnectorType {
  system_type: string;
  display_name: string;
  description: string;
  icon: string;
  base_url_hint: string;
  config_schema: { properties?: Record<string, JsonSchemaProp>; required?: string[] };
  auth_types: AuthTypeDef[];
}

export type Purpose = "read" | "write";

export interface TestStep {
  key: string;
  label: string;
  status: "ok" | "warn" | "fail" | "skip";
  detail: string;
  hint: string;
}

export interface TestResult {
  ok: boolean;
  steps: TestStep[];
  permissions: { name: string; granted: boolean | null; detail: string }[];
  info: Record<string, unknown>;
  tested_at?: string;
}

export interface CredentialSummary {
  purpose: Purpose;
  auth_type: string;
  public: Record<string, string>;
  hint: string;
  expires_at: string | null;
  days_left: number | null;
  expiring: boolean;
  rotated_at: string | null;
  last_test_at: string | null;
  last_test_ok: boolean | null;
  last_test_result: TestResult | null;
}

export type ConnectionStatus = "ok" | "warning" | "error" | "untested" | "disabled" | "incomplete";

export interface ConnectionProfile {
  id: string;
  system_type: string;
  system_name: string;
  name: string;
  base_url: string;
  environment: "prod" | "test";
  enabled: boolean;
  config: Record<string, unknown>;
  notes: string;
  version: number;
  created_at: string;
  updated_at: string;
  used_by: { id: string; name: string }[];
  credentials: Record<Purpose, CredentialSummary | null>;
  status: ConnectionStatus;
  status_text: string;
  /** Set by PATCH when the server address changed and the stored credentials were deleted. */
  credentials_cleared?: boolean;
}

export interface AuditEntry {
  id: number;
  at: string;
  actor_upn: string | null;
  actor_ip: string | null;
  module_id: string;
  system_type: string | null;
  action: string;
  target_type: string | null;
  target_id: string | null;
  target_label: string | null;
  before: Record<string, unknown> | null;
  after: Record<string, unknown> | null;
  result: "success" | "failure" | "denied";
  message: string | null;
  correlation_id: string | null;
}

export interface Role {
  id: string;
  code: string;
  name: string;
  description: string;
  is_system: boolean;
  all_permissions: boolean;
  permissions: string[];
  user_count: number;
}

export interface PermissionGroup {
  module: string;
  label: string;
  permissions: { code: string; description: string }[];
}

export interface AppUser {
  id: string;
  upn: string;
  display_name: string;
  email: string | null;
  is_active: boolean;
  is_local: boolean;
  locked: boolean;
  last_login_at: string | null;
  role_ids: string[];
}

export interface DashboardWidget<T = unknown> {
  key: string;
  title: string;
  kind: string;
  data: T | null;
  error: string | null;
}
