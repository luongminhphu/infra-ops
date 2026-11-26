# Phase 0 – Hiện trạng, kiến trúc đề xuất & câu hỏi mở

> Trạng thái: **DRAFT – chờ review**. Chưa có dòng code nào được viết. Sau khi anh/chị chốt các mục
> ở §9 (Câu hỏi mở), Phase 1 mới bắt đầu.

---

## 1. Hiện trạng repo

| Hạng mục | Hiện trạng |
|---|---|
| Commit | 1 commit `Initial commit` trên `main` |
| File | Chỉ có `README.md` (1 dòng `# infra-ops`) |
| Stack / dependency | Chưa có |
| Build / deploy / CI | Chưa có |
| `CLAUDE.md`, convention | Chưa có |

**Kết luận:** repo trống, không có stack hay convention nào cần giữ. Toàn bộ stack đề xuất trong yêu cầu
được áp dụng (chi tiết §3). Convention sẽ được định nghĩa ở Phase 1 và ghi vào `CLAUDE.md`.

---

## 2. Giả định (cần xác nhận hoặc sửa)

| # | Giả định |
|---|---|
| A1 | App chạy **on-prem**, trong mạng thấy được Domain Controller (LDAPS 636), server BioStar, SDP (nếu on-prem), và có egress ra `graph.microsoft.com` / `login.microsoftonline.com`. |
| A2 | Có sẵn (hoặc được phép dựng) PostgreSQL ≥ 15. Không bắt buộc Redis (xem §3.3). |
| A3 | Người dùng app là team IT (khoảng vài chục người). Tải thấp: vài chục Job/tuần, vài nghìn nhân viên trong đối soát → 1 instance API + 1 worker là đủ; không cần thiết kế phân tán. |
| A4 | Mã NV là duy nhất trong toàn công ty, không bao giờ tái sử dụng cho người khác. |
| A5 | Một nhân viên có tối đa 1 tài khoản chính (không xử lý tài khoản admin phụ `adm.xxx` trong Module 1). |
| A6 | ~~Không có Exchange on-prem hybrid~~ → **Sai**: ảnh EAC cho thấy có Exchange hybrid. Xem §11.2. |
| A7 | UI chỉ dành cho máy tính (laptop 13" trở lên). Mobile chỉ cần không vỡ layout, không tối ưu. |
| A8 | Font chữ & asset tải về đóng gói cùng app (không phụ thuộc CDN/Google Fonts lúc chạy). |
| A9 | Biên bản in khổ A4, font chữ theo mẫu thật (thường là Times New Roman) → container LibreOffice phải có font này. |

---

## 3. Stack đề xuất

### 3.1 Backend
- **Python 3.12 + FastAPI**, Pydantic v2, **SQLAlchemy 2.0** (async), **Alembic**, PostgreSQL.
- Tích hợp: `httpx` (SDP, BioStar, Graph REST), `msal` (token Graph – client secret/certificate),
  `ldap3` (AD LDAPS), `pypsrp` (tuỳ chọn – WinRM tới server Entra Connect).
- Biên bản: `docxtpl` (Jinja2 trong DOCX) + **LibreOffice headless** (DOCX → PDF) chạy trong image worker.
- Mã hoá secret: `cryptography` – **AES-256-GCM**, keyring có version (`MASTER_KEYS=v2:…,v1:…`)
  để rotate master key; AAD = `credential_id|purpose` để chống tráo ciphertext giữa các bản ghi.
- Log: `structlog` JSON, luôn kèm `correlation_id`, `module`, `job_id`, `step_key`.
- Test: `pytest`, `respx` (mock httpx), mock LDAP (`ldap3` MOCK_SYNC strategy), Postgres thật qua testcontainers.
- Kiểm soát ranh giới module: **`import-linter`** chạy trong CI – module không được import module khác.

### 3.2 Frontend
- **React 18 + Vite + TypeScript + Tailwind CSS + shadcn/ui** (Radix primitives).
  - Lý do chọn shadcn/ui: component được copy vào repo (ta sở hữu code, chỉnh theo design token),
    xây trên Radix nên keyboard/ARIA đúng sẵn cho dialog, menu, combobox – quan trọng vì yêu cầu WCAG AA.
    Không khoá vào theme của một UI kit lớn (MUI/Ant) vốn khó làm ra bản sắc riêng.
- **TanStack Table** (DataTable: sort, filter, sticky header, virtualize cho đối soát vài nghìn dòng),
  **TanStack Query** (cache/loading/error), **React Router**, `react-hook-form` + `zod`,
  `date-fns` + `date-fns-tz` (dd/MM/yyyy, Asia/Ho_Chi_Minh), `lucide-react` (icon), `sonner` (toast).
- Biểu đồ Dashboard: Recharts (ít, chủ yếu KPI tile + bar).

### 3.3 Job nền – đề xuất **không dùng Redis/Celery**
Workflow có các bước chờ dài (chờ sync vài chục phút, chờ ký vài ngày, lên lịch theo ngày nghỉ việc).
Trạng thái phải nằm trong DB dù có hàng đợi hay không. Vì vậy:

- **PostgreSQL là hàng đợi**: worker lặp `SELECT … FROM job_step WHERE status='pending' AND next_run_at <= now()
  FOR UPDATE SKIP LOCKED LIMIT n`. Retry/backoff = cập nhật `next_run_at`. Lên lịch = đặt `next_run_at`.
- Tác vụ định kỳ (poll đối soát, cảnh báo secret hết hạn) = bảng `scheduled_task` cùng cơ chế.
- Ưu điểm: bớt 1 thành phần hạ tầng, transaction cùng DB (không mất job khi crash giữa chừng),
  dễ debug bằng SQL. Tải của ta (A3) thấp hơn nhiều so với giới hạn của cách này.
- Nếu sau này cần throughput lớn, interface `TaskQueue` ở core cho phép thay bằng Celery/RQ mà module không đổi.

### 3.4 Đóng gói
- `docker-compose` (api, worker, web, postgres) cho local; Helm chart cho K8s.
- Image `worker` chứa LibreOffice + font; image `api` gọn nhẹ.
- File lưu qua interface `FileStorage` (mặc định: volume/PVC; sau này S3/MinIO).

---

## 4. Kiến trúc Platform Core + Modules

### 4.1 Cấu trúc thư mục

```
infra-ops/
├── backend/
│   ├── app/
│   │   ├── main.py                 # tạo FastAPI app, nạp module theo ENABLED_MODULES
│   │   ├── core/                   # ── PLATFORM CORE ──
│   │   │   ├── config.py           # env: DB, MASTER_KEYS, OIDC, ENABLED_MODULES…
│   │   │   ├── db/                 # engine, session, Base, mixins
│   │   │   ├── auth/               # OIDC (Entra), session cookie, current_user
│   │   │   ├── rbac/               # Permission registry, require("lifecycle.job.execute")
│   │   │   ├── audit/              # audit.record(...) – append-only, che secret
│   │   │   ├── secrets/            # AES-GCM keyring, mask, rotate
│   │   │   ├── connections/        # ConnectionProfile CRUD, Test Connection API
│   │   │   ├── connectors/         # ── adapter dùng chung ──
│   │   │   │   ├── registry.py     # @register_connector("entra") …
│   │   │   │   ├── base.py         # Connector protocol + ConnectionTestResult
│   │   │   │   ├── entra/  ad/  sdp/  biostar/  winrm/
│   │   │   ├── workflow/           # Job/JobStep engine, StepDefinition, runner, worker loop
│   │   │   ├── scheduler/          # scheduled_task
│   │   │   ├── documents/          # template versioning, render docxtpl, PDF, số biên bản
│   │   │   ├── storage/            # FileStorage
│   │   │   ├── notifications/
│   │   │   ├── dashboard/          # widget registry
│   │   │   └── modules/            # ModuleManifest + loader
│   │   └── modules/
│   │       └── lifecycle/          # ── MODULE 1 ──
│   │           ├── manifest.py     # id, permissions, nav, routers, workflows, widgets, connectors cần dùng
│   │           ├── models.py
│   │           ├── api/
│   │           ├── workflows/      # onboard.py, offboard.py (danh sách StepDefinition)
│   │           ├── steps/          # từng step: assign_assets, create_entra_user, wait_ad_sync…
│   │           ├── reconciliation/ # collectors + rules
│   │           └── naming.py       # ten.ho, bỏ dấu, hậu tố số
│   ├── migrations/
│   │   ├── core/versions/          # Alembic branch "core"
│   │   └── lifecycle/versions/     # Alembic branch "lifecycle", depends_on core
│   └── tests/
├── frontend/
│   └── src/
│       ├── core/                   # shell, router, auth, api client, design-system, components dùng chung
│       └── modules/lifecycle/      # manifest.ts (routes, nav items, widgets) + pages
├── deploy/  (docker/, helm/)
└── docs/    (architecture.md, adding-a-module.md, design-system.md, …)
```

### 4.2 Hợp đồng module (Module contract)

```python
# backend/app/modules/lifecycle/manifest.py  (phác thảo)
manifest = ModuleManifest(
    id="lifecycle",
    name="Vòng đời nhân sự IT",
    permissions=[
        Permission("lifecycle.job.view", "Xem Job"),
        Permission("lifecycle.job.execute", "Thực thi Job"),
        Permission("lifecycle.job.override_close", "Đóng Job không có biên bản"),
        Permission("lifecycle.reconciliation.view", "Xem đối soát"),
        Permission("lifecycle.reconciliation.sync", "Đồng bộ thủ công"),
        Permission("lifecycle.templates.manage", "Quản lý mẫu biên bản"),
    ],
    routers=[jobs_router, reconciliation_router, templates_router],
    workflows=[ONBOARD_WORKFLOW, OFFBOARD_WORKFLOW],
    connectors_used=["sdp", "entra", "ad", "biostar", "winrm"],
    scheduled_tasks=[ScheduledTask("lifecycle.reconcile", every="30m", handler=run_reconciliation)],
    dashboard_widgets=[...],
    document_kinds=[DocumentKind("handover", prefix="BG"), DocumentKind("return", prefix="TH")],
    migrations_branch="lifecycle",
)
```

Quy tắc:
1. Module chỉ import `app.core.*` (public API). `import-linter` chặn `app.modules.X` → `app.modules.Y`.
2. Bảng của module có tiền tố `<module_id>_`; migration nằm trong branch Alembic riêng.
3. Bật/tắt bằng `ENABLED_MODULES=lifecycle,...`. Module tắt → không mount router, ẩn menu, worker bỏ qua step/task của module đó (dữ liệu giữ nguyên).
4. Frontend: mỗi module export `manifest.ts`; shell dựng sidebar/route từ danh sách module mà API `/api/core/modules` trả về (đã lọc theo quyền).

### 4.3 Connector (adapter) – interface chung

```python
class Connector(Protocol):
    system_type: ClassVar[str]                     # "entra", "ad", "sdp", "biostar"
    config_schema: ClassVar[type[BaseModel]]       # field không bí mật → sinh form Settings
    secret_schema: ClassVar[type[BaseModel]]       # field bí mật
    supported_auth: ClassVar[list[AuthType]]

    async def test_connection(self) -> ConnectionTestResult   # DNS → TLS → auth → quyền thực tế
```

Năng lực theo "capability" (protocol nhỏ), connector khai báo cái nó hỗ trợ:
`DirectoryReader` (`get_by_employee_id`, `list_users`), `DirectoryWriter` (`create_user`, `disable_user`, `move`…),
`LicenseManager`, `AssetManager`, `TicketSystem` (worklog, attachment, status), `AccessControl`.

- Connector được khởi tạo **lúc runtime** từ Connection Profile + credential đã giải mã, với
  `purpose=read|write`. Xin `write` khi profile chỉ có `read` → `CapabilityUnavailable("Chưa cấu hình credential Write")` → UI khoá nút và hiện lý do.
- HTTP client dùng chung: timeout, retry + backoff + jitter, tôn trọng `Retry-After` (Graph 429/503, SDP), log request đã che secret.
- Form Settings sinh từ `config_schema`/`secret_schema` (JSON Schema) → thêm hệ thống mới (vCenter, Veeam…) không sửa UI core.

### 4.4 Workflow engine (core)

```
JobStatus:  draft → scheduled → running → waiting → completed | failed | cancelled
StepStatus: pending → in_progress → waiting → done | failed | skipped
```

- `StepDefinition` (trong module): `key`, `title`, `kind=auto|manual`, `depends_on`, `danger` (cần confirm nhập lại Mã NV),
  `when(ctx)` (điều kiện sinh step, vd. chế độ Cloud first/On-prem first), `preview(ctx)` → mô tả thay đổi dự kiến,
  `execute(ctx)` → `Done | Waiting(next_check_in) | Failed(reason, retryable)`, `poll` cho step chờ.
- **Idempotent**: mỗi step trước khi ghi đều đọc trạng thái thực tế (vd. user với `employeeId` đã tồn tại → ghi nhận và Done, không tạo lại). Job có `idempotency_key = module:type:employee_id:sdp_request_id` (unique).
- **Dry-run / Preview**: chạy `preview()` của mọi step bằng credential Read, hiện diff trước khi bấm Thực thi.
- **Không rollback tự động**: trước mỗi thay đổi, step lưu `before_state` (OU cũ, group membership, license, trạng thái tài sản) vào `job_step.before_state` + audit log.
- Step "Chờ đồng bộ": `Waiting` với backoff (1, 2, 4… tối đa N phút), quá timeout → `Failed` (retryable) để Retry thủ công.
- Job chỉ `completed` khi tài liệu bắt buộc ở trạng thái `signed`, trừ khi người có `…override_close` nhập lý do (ghi audit).

---

## 5. Data model

### 5.1 Core

| Bảng | Cột chính | Ghi chú |
|---|---|---|
| `core_user` | id, entra_oid (unique), upn, display_name, is_active, last_login_at | Tạo khi đăng nhập SSO lần đầu |
| `core_role` | id, code (unique), name, is_system | Admin, Helpdesk, Viewer |
| `core_permission` | code (PK), module_id, description | Đồng bộ từ manifest khi khởi động |
| `core_role_permission` | role_id, permission_code | |
| `core_user_role` / `core_group_role` | user_id hoặc entra_group_id → role_id | Map nhóm Entra → role (xem Q12) |
| `core_connection_profile` | id, system_type, name, base_url, environment (prod/test), enabled, config jsonb, notes, created/updated_by | `config` = field không bí mật + mapping |
| `core_credential` | id, profile_id, purpose (read/write), auth_type, ciphertext, nonce, key_version, hint_last4, expires_at, rotated_at, last_test_at, last_test_result jsonb | unique(profile_id, purpose) |
| `core_job` | id, module_id, workflow_key, subject_key (Mã NV), external_ref (SDP request id), status, params jsonb, scheduled_for, idempotency_key (unique), created_by, closed_at, close_override_reason | |
| `core_job_step` | id, job_id, seq, step_key, kind, status, attempts, next_run_at, started_at, finished_at, actor_id, before_state jsonb, output jsonb, error jsonb | index (status, next_run_at) |
| `core_job_event` | id, job_id, step_id, at, level, message, data jsonb | Log hiển thị trên UI |
| `core_scheduled_task` | key, module_id, interval, next_run_at, last_status, enabled | |
| `core_document_template` | id, module_id, kind, name | |
| `core_template_version` | id, template_id, version, file_id, placeholders jsonb, is_active, uploaded_by, uploaded_at | Không xoá, chỉ thêm version |
| `core_document` | id, module_id, kind, number (unique), job_id, template_version_id, status (draft/rendered/signed), data_snapshot jsonb, pdf_file_id, signed_file_id, signed_at | Snapshot dữ liệu lúc render để in lại y hệt |
| `core_document_sequence` | prefix, period (YYYYMM), last_value | `UPDATE … RETURNING` trong transaction → `BG-202611-0001` |
| `core_file` | id, sha256, size, mime, storage_key, uploaded_by | |
| `core_audit_log` | id (bigserial), at, actor_id, actor_ip, module_id, system_type, action, target_type, target_id, before jsonb, after jsonb, result, correlation_id, prev_hash, hash | Bất biến: trigger chặn UPDATE/DELETE + chuỗi hash để phát hiện sửa trực tiếp DB |
| `core_notification` | id, user_id/role, kind, payload, read_at | |

### 5.2 Module 1 – `lifecycle_*`

| Bảng | Cột chính |
|---|---|
| `lifecycle_employee` | employee_id (unique), full_name, department, job_title, manager_employee_id, upn, account_type, status, last_seen_sources jsonb |
| `lifecycle_request` | sdp_request_id (unique), type (onboard/offboard), employee_id, account_type (office365/user_account), effective_date, sdp_status, raw jsonb, job_id |
| `lifecycle_snapshot_run` | id, system_type, trigger (scheduled/manual), started_at, finished_at, status, record_count, error |
| `lifecycle_system_record` | run_id, system_type, employee_id, external_id, data jsonb, data_hash — 1 dòng / nhân viên / hệ thống / lần chụp |
| `lifecycle_asset_snapshot` | run_id, sdp_asset_id, asset_tag, serial, product_type, model, state, assigned_employee_id |
| `lifecycle_asset_movement` | job_id, sdp_asset_id, action (assign/return), condition (tốt/hỏng/thiếu phụ kiện/thiếu), note, confirmed_by, confirmed_at — nguồn dữ liệu cho bảng tài sản trong biên bản |
| `lifecycle_reconciliation_run` | id, started_at, snapshot_run_ids, summary jsonb |
| `lifecycle_reconciliation_row` | run_id, employee_id, cells jsonb (mỗi hệ thống 1 ô), issue_count, max_severity |
| `lifecycle_reconciliation_issue` | run_id, employee_id, rule_code, system_type, severity, detail jsonb, first_seen_run_id, acknowledged_by, ack_note |

Snapshot chỉ giữ N ngày chi tiết (cấu hình được), các lần cũ hơn chỉ giữ `summary` để xem lịch sử xu hướng.

---

## 6. API endpoint (phác thảo)

Core (`/api/core`):
```
GET  /auth/login  /auth/callback  POST /auth/logout   GET /me
GET  /modules                                  # module bật + nav theo quyền
GET  /connectors                               # loại hệ thống + JSON schema form
GET|POST /connections   GET|PATCH|DELETE /connections/{id}
PUT  /connections/{id}/credentials/{purpose}   # nhập / thay mới secret (không bao giờ trả về)
POST /connections/{id}/test?purpose=read|write
GET  /connections/export   POST /connections/import         # không kèm secret
GET  /jobs  GET /jobs/{id}  GET /jobs/{id}/events
POST /jobs/{id}/preview  /jobs/{id}/execute  /jobs/{id}/steps/{key}/retry|skip|confirm
POST /jobs/{id}/close  (override: {reason})
GET|POST /templates   POST /templates/{id}/versions   GET /templates/placeholders
POST /documents/{id}/render   GET /documents/{id}/pdf   POST /documents/{id}/signed
GET  /audit   GET /dashboard/widgets
GET|PUT /roles  /roles/{id}/permissions   GET|PUT /users/{id}/roles
GET  /health/live  /health/ready
```

Module 1 (`/api/lifecycle`):
```
POST /requests/import            # kéo request Onboard/Offboard mới từ SDP
GET  /requests   POST /requests/{id}/job           # tạo Job (chọn chế độ, override)
GET  /assets/in-store?type=laptop                   # chọn tài sản In Store
GET  /reconciliation?filter=…   GET /reconciliation/{employee_id}
POST /reconciliation/sync?systems=…                 # sync thủ công
GET  /reconciliation/export?format=xlsx|csv
POST /reconciliation/issues/{id}/acknowledge
GET|PUT /settings                                   # chế độ tạo TK mặc định, timeout, OU…
```

---

## 7. Phân tích Hybrid: Cloud → On-prem và source of authority

> Đã được cập nhật theo câu trả lời vòng 1. **Xem §11.2 và §11.3**: các mục bên dưới vẫn đúng về nguyên tắc, nhưng cách tạo tài khoản và thứ tự Offboard được chốt lại ở §11.

### 7.1 Điểm cần làm rõ về "Cloud first"
Entra Connect Sync **không đồng bộ user từ Entra xuống AD** (user writeback đã bị bỏ). Các cơ chế có thể đang chạy:

| Cơ chế | Kết quả sau khi "đồng bộ xuống" | Source of authority sau đó |
|---|---|---|
| (a) Script/tool tự viết (PowerShell đọc Graph → `New-ADUser`) rồi Entra Connect **soft-match** theo UPN/proxyAddress | Một object AD được tạo, Entra Connect nối với user cloud | **AD** (`onPremisesSyncEnabled = true`) |
| (b) API-driven inbound provisioning (app/HR gọi `/bulkUpload` → provisioning agent tạo user trong AD) | Thực chất là **On-prem first qua API** | AD |
| (c) Công cụ bên thứ 3 / Cloud Sync cho group | Cloud Sync chỉ provision **group** xuống AD, không phải user | — |
| (d) Không có gì – user cloud-only, AD tạo tay sau | Có thể có 2 object không liên kết | Không xác định |

Adapter sẽ không phụ thuộc cứng: step "Chờ đồng bộ xuống AD" chỉ **poll AD theo `employeeID`/UPN** và tuỳ chọn
kiểm tra `onPremisesSyncEnabled` trên Entra chuyển sang `true` (dấu hiệu đã soft-match). Nếu là (b), app có thể
gọi thẳng API inbound provisioning như một chế độ thứ ba.

**Lưu ý soft-match:** nếu tenant bật `BlockSoftMatch`, hoặc user có role admin, soft-match sẽ không xảy ra → 2 object trùng. Cần biết cấu hình này.

### 7.2 Offboard theo source of authority
Step disable đọc `onPremisesSyncEnabled` của user tại thời điểm chạy:

- **Synced (AD là nguồn):**
  1. `revokeSignInSessions` qua Graph ngay (cắt phiên đăng nhập tức thì, được phép với user synced).
  2. Ghi OU cũ + group membership vào `before_state` + audit.
  3. Disable trên AD + move vào `Ban_Users`.
  4. (Tuỳ chọn) trigger `Start-ADSyncSyncCycle -PolicyType Delta` qua WinRM.
  5. Poll Graph tới khi `accountEnabled = false` (Waiting, có timeout).
  - Không PATCH `accountEnabled` trên Entra cho user synced: Graph từ chối hoặc sync ghi đè.
- **Cloud-only:** PATCH `accountEnabled=false` qua Graph; nếu có object AD tương ứng (không liên kết) thì cũng disable + move.

### 7.3 Rủi ro OU `Ban_Users` ngoài phạm vi sync
Nếu `Ban_Users` không nằm trong phạm vi OU của Entra Connect, khi move vào đó Entra Connect sẽ **xoá** user trên Entra
(vào Deleted users 30 ngày) → mất license, mailbox chuyển soft-deleted, **không còn chuyển sang shared mailbox được**.
Do đó:
- Settings: nút "Kiểm tra phạm vi sync" (đọc cấu hình qua WinRM nếu có, hoặc admin tự khai báo) + cảnh báo đỏ.
- Thứ tự step Offboard: **xử lý mailbox (shared/forward) và license TRƯỚC** bước move OU.
- Preview hiển thị cảnh báo rõ nếu phạm vi sync chưa được xác nhận.

### 7.4 Mailbox shared / forward
Microsoft Graph **không** hỗ trợ chuyển mailbox sang shared hay đặt forwarding cấp mailbox.
Cần **Exchange Online PowerShell** (app-only, certificate, quyền `Exchange.ManageAsApp` + role Exchange):
`Set-Mailbox -Type Shared` / `-ForwardingSmtpAddress`. Đề xuất: worker image cài `pwsh` + module `ExchangeOnlineManagement`,
đóng gói thành connector `exo`. Cần xác nhận có làm bước này trong Phase 5 không (Q9).

### 7.5 Các lưu ý Graph khác
- `usageLocation` phải được set **trước** khi gán license.
- Group-based licensing: kiểm tra `licenseAssignmentStates` để biết license đã thực sự được áp (có thể lỗi do hết license).
- Mailbox provisioned: kiểm tra `GET /users/{id}/mailboxSettings` thành công (thường 5–30 phút sau khi gán license).
- Quyền tối thiểu: có thể dùng `GroupMember.ReadWrite.All` thay `Group.ReadWrite.All` nếu chỉ thêm/bớt thành viên.
- App không thể disable user đang giữ role admin nếu không có quyền đặc quyền cao → step sẽ báo lỗi rõ ràng.

---

## 8. Design direction

> **Nguồn:** `ui-ux-pro-max` (bản đầy đủ từ `nextlevelbuilder/ui-ux-pro-max-skill`, commit `e59ff37`) kết hợp
> `frontend-design`. Kết quả tra cứu và quyết định chấp nhận/loại bỏ ở §8.0.
> Các skill khác dùng ở các phase sau: `modern-web-guidance`, `dataviz`, `design:accessibility-review`,
> `design-skills:accessibility-audit`, `design:ux-copy`, `design-skills:ux-writing`, `design:design-critique`.

### 8.0 Kết quả `ui-ux-pro-max` và quyết định

Lệnh: `--design-system "internal IT operations admin console data-dense" --density 8 --motion 2 --variance 3`,
bổ sung tra cứu theo domain `style`, `typography`, `color`, `chart`, `ux` và `--stack shadcn`.

| Hạng mục | Skill đề xuất | Quyết định | Lý do |
|---|---|---|---|
| Style | **Data-Dense Dashboard** + Minimalism & Swiss Style | ✅ Áp dụng | Đúng loại sản phẩm: bảng dày đặc, sticky header, padding 8–12px, KPI tile, rủi ro accessibility thấp |
| Biến mật độ | Hàng bảng 36px, sidebar 240px, header 56px, gap 8px | ✅ Áp dụng hàng 36px làm mặc định, sidebar 240px; topbar giữ 48px | 36px vẫn dày đặc mà dễ đọc dấu tiếng Việt hơn 32px |
| Typography | **"Vietnamese Friendly": Be Vietnam Pro + Noto Sans** | ✅ Áp dụng; Noto Sans làm font dự phòng | Trùng với lựa chọn ban đầu, nay có xác nhận từ dữ liệu của skill |
| Typography (phương án 2) | Fira Code + Fira Sans ("Dashboard Data") | ❌ Không dùng | Dùng mono cho tiêu đề không hợp UI tiếng Việt; mono chỉ dùng cho giá trị định danh |
| Màu | Navy `#1E40AF` + accent amber `#D97706` | ⚠️ Không dùng nguyên bản, giữ **petrol** (§8.2) | Amber trùng màu trạng thái "Waiting", xanh dương trùng "In Progress": nút bấm sẽ bị nhầm với trạng thái. Em giữ petrol; anh/chị chốt ở Q18 |
| Màu trạng thái | Bảng "Status Page": xanh lá vận hành / đỏ sự cố / amber bảo trì | ✅ Khớp với bảng trạng thái §8.2 | |
| Pattern | "Enterprise Gateway" (landing page: hero, logo khách hàng, Contact Sales) | ❌ Bỏ qua | Pattern cho trang marketing, không áp dụng cho app nội bộ |
| Motion | GSAP scroll reveal | ❌ Bỏ qua | App vận hành không cần hiệu ứng khi cuộn; chỉ dùng transition 150–200ms cho phản hồi thao tác, tôn trọng `prefers-reduced-motion` |
| Chart | **Bullet chart** cho KPI so với ngưỡng | ✅ Dùng cho widget "License còn trống theo SKU" (đã dùng / tổng); luôn kèm số và bảng dự phòng | |
| UX | Xác nhận hành động huỷ/không hoàn tác; toast khi thành công; bulk action (cột checkbox + action bar) | ✅ Áp dụng; bulk action cho Đối soát ("Đánh dấu đã biết" nhiều dòng) | |
| shadcn | `Table` ngữ nghĩa (thead/tbody); DataTable = shadcn Table + TanStack Table | ✅ Áp dụng | |

Checklist trước khi bàn giao (từ skill): không dùng emoji làm icon (dùng Lucide), có `cursor-pointer`, transition hover 150–300ms,
contrast ≥ 4.5:1, focus nhìn thấy rõ, tôn trọng `prefers-reduced-motion`, không vỡ layout ở các mốc 1024/1440px.

### 8.1 Ý tưởng: "Bàn điều phối" — mỗi nhân viên là một **đường ray hệ thống**
Câu hỏi cốt lõi của team mỗi ngày: *"Người này đã khớp ở mọi hệ thống chưa?"*. Vì vậy chi tiết đáng nhớ duy nhất
của giao diện là **System Rail**: một dải 7 ô cố định, luôn cùng thứ tự, cùng vị trí:

```
 SDP   Tài sản   Entra   AD   M365   BioStar   Biên bản
 [✓]    [✓]      [✓]   [◷]   [✓]    [✕]       [–]
```

Rail xuất hiện ở khắp nơi với cùng một ngữ nghĩa: hàng trong bảng Đối soát, tiêu đề trang Chi tiết Job (stepper
gom theo hệ thống), cột thu gọn trong Danh sách Job, và widget Dashboard. Nhìn một lần là đọc được trạng thái.
Mọi thứ còn lại giữ yên lặng, kỷ luật: không gradient, không bóng đổ trang trí, không card chồng card.

### 8.2 Màu (token)

| Token | Light | Dark | Dùng cho |
|---|---|---|---|
| `ink` | `#1C2430` | `#E6EAF0` | Chữ chính |
| `paper` | `#F6F7F9` | `#161B22` | Nền app |
| `surface` | `#FFFFFF` | `#1E242D` | Bảng, panel |
| `line` | `#DDE2E8` | `#2E3642` | Viền, kẻ bảng |
| `petrol` (primary) | `#0F5F6B` | `#4FB3BF` | Nút chính, link, focus ring |
| `muted` | `#5B6676` | `#9AA5B4` | Chữ phụ |

Màu trạng thái (luôn đi kèm icon + nhãn, không bao giờ chỉ dùng màu):

| Trạng thái | Màu | Icon (lucide) |
|---|---|---|
| Done / Khớp | `#1E7B4F` | `check-circle-2` |
| In Progress | `#2459C6` | `loader-circle` |
| Waiting | `#A35F00` | `clock` |
| Failed | `#BF2F2F` | `x-circle` |
| Skipped | `#667085` | `minus-circle` |
| Lệch | `#A3245A` | `alert-triangle` |

Primary dùng **petrol** (xanh dầu) thay cho xanh cobalt mặc định của mọi SaaS, đồng thời để
xanh dương dành riêng cho "In Progress" — tránh nhầm "nút bấm" với "đang chạy". Mọi cặp màu sẽ được
kiểm tra contrast ≥ 4.5:1 (chữ) / 3:1 (icon, viền) ở cả hai chế độ trước khi chốt ở Phase 1.

### 8.3 Chữ
- **Be Vietnam Pro** — font giao diện. Được thiết kế bởi người Việt, dấu tiếng Việt cân đối, không bị chồng dấu
  ở cỡ nhỏ (vấn đề thường gặp với Inter/Roboto ở 13px trong bảng dày đặc). Bật `tabular-nums` cho cột số.
- **JetBrains Mono** — **chỉ** cho giá trị định danh mà người dùng hay so sánh/copy: Mã NV, UPN, serial,
  asset tag, DN, số biên bản. Không dùng cho nhãn.
- Thang chữ: 12 (chú thích tối thiểu) · 13 (ô bảng) · 14 (nội dung) · 16 (tiêu đề panel) · 20 (tiêu đề trang) · 28 (KPI).
  Sentence case, không viết hoa toàn bộ nhãn.
- Font được đóng gói qua `@fontsource` (A8).

### 8.4 Layout & mật độ
- Sidebar 240px (thu gọn còn 56px), nhóm theo module; topbar 48px (tìm kiếm Mã NV toàn cục `Ctrl+K`, theme, user).
- Spacing gốc 4px; bảng có 2 mật độ: **Gọn** (dòng 36px, mặc định – theo `ui-ux-pro-max`) / **Thoáng** (44px).
- Căn trái toàn bộ; số căn phải; cột định danh cố định bên trái khi cuộn ngang.
- Radius phân cấp: 4px (ô, badge), 6px (input, nút), 8px (dialog, panel) — không dùng một radius cho mọi thứ.

### 8.5 Wireframe

**Đối soát theo Mã NV**
```
┌ Sidebar ─┬──────────────────────────────────────────────────────────────────────────┐
│ Tổng quan│ Đối soát › Theo Mã NV                      Lần đồng bộ: 16/11/2026 14:30 │
│          │ [Tìm Mã NV, tên…] [Phòng ban ▾] [Chỉ dòng lệch ☑] [Loại lệch ▾]          │
│ Vòng đời │                                      [Đồng bộ ngay] [Xuất Excel ▾]       │
│  Job     │ ┌──────────┬──────────────┬─SDP─┬Tài sản┬Entra┬─AD─┬M365┬BioStar┬BB─┐ │
│  Đối soát│ │ NV00123  │ Nguyễn Văn An│  ✓  │ ✓ 3  │  ✓  │ ◷  │ ✓  │  ✕    │ –  │ │
│  Mẫu BB  │ │ NV00124  │ Trần Thị Bình│  ✓  │ ⚠ 1  │  ⚠  │ ⚠  │ ⚠  │  ⚠    │ ✓  │ │
│          │ └──────────┴──────────────┴─────┴──────┴─────┴────┴────┴───────┴────┘ │
│ Hệ thống │                                    ┌ Side panel: NV00124 ───────────┐ │
│  Kết nối │                                    │ Đã offboard 10/11/2026         │ │
│  Audit   │                                    │ ⚠ Entra: vẫn enabled           │ │
│  Quyền   │                                    │ ⚠ AD: chưa ở OU Ban_Users      │ │
└──────────┴────────────────────────────────────└ [Mở Job] [Đánh dấu đã biết]  ──┘ │
```

**Chi tiết Job (Onboard – Cloud first)**
```
Job #ONB-000231 · NV00125 Lê Minh Châu · Onboard · Cloud first     [Xem trước] [Thực thi]
Rail:  SDP ✓  Tài sản ✓  Entra ✓  AD ◷  M365 –  BioStar –  Biên bản –
┌ Các bước ───────────────────────────────┬ Nhật ký ──────────────────────────────────┐
│ ✓ 1 Nhận request SDP #45122             │ 14:02 Tạo user le.chau@… (employeeId …)  │
│ ✓ 2 Gán tài sản (3)                     │ 14:03 Bắt đầu chờ đồng bộ xuống AD       │
│ ✓ 3 Tạo tài khoản Entra                 │ 14:08 Chưa thấy trên AD, thử lại lúc 14:16│
│ ◷ 4 Chờ đồng bộ xuống AD  (lần 3/12)    │                                          │
│ – 5 Gán license Office 365              │                                          │
│ – 6 Tạo user BioStar                    │                                          │
│ – 7 Đăng ký vân tay/thẻ (thủ công)      │                                          │
│ – 8 Biên bản bàn giao (in → ký → tải lên)│                                         │
└─────────────────────────────────────────┴──────────────────────────────────────────┘
```

**Kết nối hệ thống**
```
Kết nối hệ thống                                                [Thêm kết nối]
┌──────────────────────────────────────────────────────────────────────────────┐
│ ● Microsoft Entra ID – Prod        Đang hoạt động · Kiểm tra 16/11 14:00      │
│   Read ✓ (secret hết hạn 02/01/2027)   Write ✓ (⚠ hết hạn sau 12 ngày)       │
│   Dùng bởi: Vòng đời nhân sự            [Kiểm tra kết nối] [Sửa]             │
├──────────────────────────────────────────────────────────────────────────────┤
│ ● Active Directory – Prod          ✕ Lỗi: không xác thực được bind account    │
│   Read ✓   Write – chưa cấu hình (các thao tác ghi bị khoá)                  │
└──────────────────────────────────────────────────────────────────────────────┘
```
Danh sách dạng hàng có thanh trạng thái bên trái thay vì lưới card giống nhau — mỗi hệ thống có độ ưu tiên
thông tin khác nhau, và danh sách dễ quét hơn khi có 10+ kết nối.

**Confirm hành động nguy hiểm**
```
┌ Vô hiệu hoá tài khoản và chuyển sang OU Ban_Users ─────────────────────┐
│ Trần Thị Bình (NV00124) sẽ không đăng nhập được Windows, email, Teams. │
│ OU hiện tại: OU=Sales,OU=Users,DC=corp,DC=local → OU=Ban_Users,…       │
│ 7 nhóm sẽ được ghi lại vào nhật ký trước khi thay đổi.                 │
│ Nhập Mã NV để xác nhận: [________]                                     │
│                                   [Huỷ]  [Vô hiệu hoá tài khoản]       │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 9. Câu hỏi mở

### Hybrid identity
- **Q1.** Cơ chế đưa user cloud-first xuống AD on-prem là gì (bảng §7.1: a/b/c/d hay khác)? Mất bao lâu thường lệ?
- **Q2.** User cloud-first xuất hiện ở OU nào trên AD? Có move sang OU phòng ban sau đó không?
- **Q3.** Thuộc tính nào được đồng bộ, đặc biệt `employeeId` (Entra) ↔ `employeeID` (AD)? Có dùng `employeeNumber` hay extensionAttribute nào cho Mã NV không?
- **Q4.** Sau khi đồng bộ, user có chuyển thành `onPremisesSyncEnabled = true` (AD làm nguồn) không? Tenant có bật `BlockSoftMatch` không?
- **Q5.** OU `Ban_Users` có nằm trong phạm vi sync của Entra Connect không? Muốn user đã nghỉ **còn** trên Entra (disabled) hay bị xoá?
- **Q6.** Có server Entra Connect cho phép WinRM từ app không (để trigger delta sync và đọc phạm vi OU)?

### Hệ thống
- **Q7.** SDP: bản On-prem (build mấy?) hay Cloud? Mã NV đang nằm ở field nào (user field / request UDF)? Tên template Onboard/Offboard?
- **Q8.** BioStar 2 hay BioStar X (phiên bản)? Mã NV có chữ cái không (vd. `NV00123`)? Nếu có, BioStar phải bật "alphanumeric user ID".
- **Q9.** Offboard mailbox: cần chuyển shared/forward không? Nếu có, đồng ý dùng Exchange Online PowerShell (app-only certificate) như §7.4?
- **Q10.** Có Exchange on-prem (hybrid) đang quản lý thuộc tính mail không (giả định A6)?
- **Q11.** Mapping "Office 365" / "User Account": dùng SKU nào cụ thể, gán trực tiếp hay group-based licensing?

### Nền tảng
- **Q12.** Phân quyền: đồng ý dùng **App Roles** trên App Registration đăng nhập (gán cho nhóm Entra) để xác định role, còn ma trận role → permission chỉnh trong app? Hay quản lý gán role hoàn toàn trong app?
- **Q13.** Hạ tầng triển khai: K8s có sẵn (on-prem) hay VM + docker-compose? PostgreSQL dùng chung hay dựng riêng?
- **Q14.** Đồng ý **không dùng Redis/Celery**, dùng PostgreSQL làm hàng đợi (§3.3)?
- **Q15.** Có cần thông báo (email/Teams) khi Job lỗi / chờ ký / secret sắp hết hạn không? Kênh nào?
- **Q16.** Chu kỳ poll đối soát mong muốn (đề xuất 30 phút) và thời gian giữ snapshot chi tiết (đề xuất 90 ngày)?
- **Q17.** Quy tắc đặt tên: `ten.ho` (vd. `an.nguyen`) — tên đệm có dùng không (`an.nguyenvan`)? Trùng thì `an.nguyen2` hay `an.nguyen01`? UPN domain là gì?
- **Q18.** Design direction (§8): đồng ý hướng "Bàn điều phối / System Rail", màu petrol, font Be Vietnam Pro?

---

## 10. Phạm vi Phase 1 (sau khi chốt)
1. Khung monorepo, docker-compose, CI (lint, test, import-linter), `CLAUDE.md`, README.
2. Module loader + manifest, permission registry, Alembic branch `core`.
3. SSO Entra (OIDC, session cookie httpOnly) + RBAC + màn hình Quản lý quyền.
4. Secrets (AES-GCM keyring) + connector registry + Settings – Kết nối hệ thống + Test Connection
   (Entra, AD, SDP, BioStar – mức test kết nối và liệt kê quyền).
5. Audit log bất biến + màn hình Audit.
6. Design tokens, layout shell, component dùng chung (DataTable, StatusBadge, SystemRail, Stepper,
   ConfirmDangerDialog, ConnectionRow, EmptyState, Skeleton), `docs/design-system.md`.
7. Accessibility review + design critique cho các màn hình Phase 1.

---

## 11. Phase 0 – vòng 2: quyết định đã chốt & phân tích bổ sung

### 11.1 Quyết định đã chốt

| # | Quyết định |
|---|---|
| Q3 | `employeeId` (Entra) ↔ `employeeID` (AD) **có đồng bộ**. Đây là khoá đối soát chính. |
| Q4 | Sau đồng bộ user là `onPremisesSyncEnabled = true` → **AD là source of authority** cho mọi user. |
| Q5 | OU `Ban_Users` **nằm ngoài** phạm vi sync → user move vào đó sẽ bị **xoá** trên Entra (Deleted users, 30 ngày). |
| Q7 | SDP **On-prem**. Mã NV không nằm trong field riêng mà trong **nội dung mô tả** của request (§11.4). |
| Q8 | **BioStar 2**. Mã NV chỉ gồm chữ số. |
| Q9 | Offboard luôn move sang `Ban_Users`; shared/forward mailbox chỉ làm khi quản lý yêu cầu (tuỳ chọn theo Job). |
| Q12 | SSO Entra ID (OIDC) chỉ để **xác thực**; gán role **hoàn toàn trong app**. Admin đầu tiên khai báo qua env `BOOTSTRAP_ADMIN_UPNS`. |
| Q13 | Triển khai **K8s qua Rancher** → Helm chart. |
| Q14 | **Không dùng Redis**; PostgreSQL làm hàng đợi. |
| Q17 | Quy tắc đặt tên: `<tên>.<chữ cái đầu họ + đệm>` (§11.5). |
| Q18 | Đồng ý design direction "Bàn điều phối / System Rail", màu petrol, Be Vietnam Pro. |

### 11.2 Phát hiện: có Exchange hybrid → cách tạo tài khoản thực tế là "On-prem first"

Ảnh chụp menu **New → User mailbox / Linked mailbox / Office 365 mailbox** là của **Exchange Admin Center on-prem**
(`https://<exchange-server>/ecp`) trong môi trường **Exchange hybrid**. Lựa chọn "Office 365 mailbox" chạy
`New-RemoteMailbox`, thực hiện:
1. Tạo user trên **AD on-prem**, ngay tại OU được chọn trong hộp thoại (khớp với "OU tuỳ theo ticket").
2. Ghi các thuộc tính Exchange hybrid (`msExchRemoteRecipientType`, `targetAddress = …@<tenant>.mail.onmicrosoft.com`, `proxyAddresses`).
3. Entra Connect đồng bộ user lên Entra → `onPremisesSyncEnabled = true` (khớp Q4).
4. Sau đó vào admin.microsoft.com chỉ để **gán license** → mailbox được tạo trên Exchange Online.

Như vậy quy trình hiện tại thực chất là **On-prem first qua Exchange hybrid**, không phải tạo user trên Entra rồi
"chờ đồng bộ xuống". Điều này cũng giải thích vì sao không cần cơ chế Cloud → On-prem nào.

**Hệ quả với thiết kế (đề xuất):**
- Chế độ tạo tài khoản mặc định đổi thành **"Hybrid Exchange (Remote mailbox)"**:
  `New-RemoteMailbox` (OU theo ticket, UPN, sAMAccountName, mật khẩu tạm + `ResetPasswordOnNextLogon`)
  → ghi bổ sung `employeeID`, `department`, `title`, `manager`, `mobile`, `physicalDeliveryOfficeName` qua LDAPS
  → (tuỳ chọn) trigger delta sync → **chờ user xuất hiện trên Entra** (poll Graph theo `employeeId`)
  → set `usageLocation=VN` + gán license qua Graph → chờ mailbox EXO sẵn sàng.
- **Không tạo user AD trần qua LDAP** khi có Exchange hybrid: thiếu thuộc tính remote mailbox → gán license sẽ sinh
  mailbox cloud không được Exchange on-prem quản lý, gây lỗi khi chỉnh địa chỉ email về sau.
- **Không tạo user trên Entra qua Graph `POST /users`**: user cloud-only không có object AD tương ứng; trái với cách đang vận hành.
  Chế độ "Cloud first" vẫn giữ trong code ở dạng tắt, phòng khi sau này bỏ Exchange on-prem.
- Cần connector mới **`exchange`** (Exchange Management Shell từ xa qua WinRM, dùng `pypsrp`, endpoint
  `http(s)://<exchange>/PowerShell`, `ConfigurationName=Microsoft.Exchange`). Đề xuất an toàn hơn: dựng một
  **JEA endpoint** trên server quản trị chỉ cho phép đúng các lệnh cần dùng (`New-RemoteMailbox`, `Get-RemoteMailbox`,
  `Set-RemoteMailbox`, `Enable-RemoteMailbox`) → service account của app không có quyền Exchange admin đầy đủ.
- Shared mailbox trong hybrid: `Set-RemoteMailbox -Type Shared` ở on-prem + `Set-Mailbox -Type Shared` ở Exchange Online
  (cần Exchange 2016 CU21 / 2019 CU10 trở lên). Forward: `Set-Mailbox -ForwardingSmtpAddress` ở EXO.

### 11.3 Offboard: thứ tự bước khi `Ban_Users` nằm ngoài phạm vi sync

Vì move vào `Ban_Users` làm user bị **xoá** trên Entra (mất mailbox, license được trả lại, chỉ khôi phục được trong 30 ngày
bằng cách move lại vào OU có sync), thứ tự Offboard phải là:

| # | Bước | Ghi chú |
|---|---|---|
| 1 | Ghi `before_state`: OU cũ, group membership, license, thuộc tính mailbox | Đủ để khôi phục thủ công |
| 2 | Graph `revokeSignInSessions` | Cắt phiên đăng nhập ngay, không chờ sync |
| 3 | Disable user trên AD | |
| 4 | Trigger delta sync (nếu có WinRM) → chờ Entra `accountEnabled=false` | Bảo đảm user bị chặn trên Entra trước khi bị xoá |
| 5 | (Tuỳ chọn) Shared / forward mailbox | **Xung đột với bước 6**, xem bên dưới |
| 6 | Move vào `Ban_Users` (confirm nhập lại Mã NV) | |
| 7 | Trigger delta sync → chờ user vào Deleted users trên Entra | Xác nhận kết quả |
| 8 | BioStar, tài sản, biên bản… | |

⚠️ **Xung đột cần quyết định (Q19):** shared mailbox và forward đều cần object user **còn tồn tại** trên Entra.
Move vào `Ban_Users` (ngoài sync) sẽ xoá luôn mailbox, kể cả mailbox đã chuyển sang shared. Phương án:
- **(a)** Tạo OU `Ban_Users_Retained` **nằm trong** phạm vi sync, dành cho user nghỉ việc cần giữ mailbox; user vẫn disabled.
  Hết thời hạn giữ (cấu hình được, vd. 90 ngày) app nhắc chuyển tiếp sang `Ban_Users`. **(Đề xuất)**
- (b) Hoãn bước move OU tới ngày hết thời hạn giữ mailbox (Job ở trạng thái Waiting tới ngày đó).
- (c) Export mailbox ra PST trước rồi mới move (thủ công, ngoài phạm vi app).

### 11.4 Request từ SDP: đọc dữ liệu từ nội dung mô tả

Request Onboard/Offboard do hệ thống **RTR (recruit transfer resign)** tạo tự động trên SDP:
- Tiêu đề: `(RTR-<số>) <Loại biến động>/<Họ tên>/<Phòng>`; requester `RTR`.
- Thông tin nhân viên nằm trong mô tả dạng "nhãn: giá trị". Ví dụ (dữ liệu giả):

```
Loại biến động: Chính thức
A. Thông tin nhân viên
1. Họ & tên: Nguyễn Văn A
2. Mã nhân viên: 001234
3. Ngày hiệu lực: 01/12/2026
4. Chức danh nội bộ: …          5. Chức danh bên ngoài: …
6. Khối: …   7. Phòng: …   8. Bộ phận: …   9. Nơi làm việc: …
10. Số điện thoại: …
11. Quản lý trực tiếp: Trần Văn B (001200)
12. Quản lý gián tiếp: …
```

Thiết kế:
- **Parser cấu hình được** (Settings → SDP → "Mapping nội dung request"): mỗi field app cần ↔ nhãn trong mô tả
  (vd. `employee_id` ↔ "Mã nhân viên"), kiểu dữ liệu, bắt buộc hay không. Hỗ trợ mô tả dạng HTML (SDP API trả HTML).
  Đổi mẫu RTR sau này chỉ cần sửa mapping, không sửa code.
- Mã NV là **chuỗi** (giữ số 0 đầu: `003551` ≠ `3551`). Quản lý trực tiếp lấy Mã NV trong ngoặc → tra `manager` trên AD.
- Thiếu field bắt buộc / không parse được → request vào trạng thái "Cần bổ sung", hiện rõ field lỗi, cho Helpdesk sửa tay (ghi audit).
- Ngày hiệu lực → dùng để lên lịch Job (Onboard và Offboard).
- **OU đích**: bảng mapping (Khối, Phòng, Bộ phận) → OU DN trong Settings; app đọc cây OU từ AD để chọn.
  Không khớp mapping → Helpdesk chọn OU trong bước Preview.
- Khuyến nghị dài hạn (ngoài phạm vi app): đề nghị team SDP/RTR thêm các trường riêng (UDF) cho Mã NV, loại tài khoản, ngày hiệu lực.

### 11.5 Quy tắc đặt tên

Ví dụ "Nguyễn Thanh Bình": tên = `binh`, chữ cái đầu họ + đệm = `nt` → `binh.nt`.
- Bỏ dấu tiếng Việt, `Đ/đ → d`, chữ thường, bỏ ký tự không hợp lệ.
- Danh sách ứng viên khi trùng (thứ tự cấu hình được trong Settings):
  `binh.nt` → `binh.nt2` → `binh.nguyen` → `binh.nguyenthanh` → `binh.nt3` …
  App đề xuất ứng viên trống đầu tiên; Helpdesk được chọn ứng viên khác hoặc nhập tay trong bước Preview.
- "Trùng" kiểm tra trên: AD `sAMAccountName`, `userPrincipalName`, `mail`, `proxyAddresses`; Entra `userPrincipalName`,
  `mail`, `proxyAddresses`, **kể cả user đã xoá (Deleted users) và user trong `Ban_Users`** (không tái sử dụng username của người đã nghỉ).
- Giới hạn `sAMAccountName` 20 ký tự → nếu vượt, chuyển sang ứng viên kế tiếp.

### 11.6 Câu hỏi còn mở (vòng 2)

- **Q1'.** Xác nhận: Helpdesk tạo user bằng **Exchange Admin Center on-prem** (`https://<exchange>/ecp`, đăng nhập `DOMAIN\user`),
  New → **Office 365 mailbox**, chọn OU trong hộp thoại, rồi mới vào admin.microsoft.com để gán license — đúng không?
  Phiên bản Exchange Server (2016 / 2019 / SE)?
- **Q2'.** Đồng ý đổi chế độ mặc định sang **"Hybrid Exchange (Remote mailbox)"** như §11.2? Có thể dựng JEA endpoint
  (hoặc cho phép WinRM tới Exchange server bằng service account) không?
- **Q6.** Có cho phép WinRM tới server Entra Connect để trigger delta sync không? Nếu không, mỗi bước "chờ sync" sẽ chờ chu kỳ mặc định (~30 phút).
- **Q11.** SKU cụ thể cho "Office 365" và "User Account"; gán trực tiếp hay theo group?
  Loại tài khoản (Office 365 / User Account) nằm ở đâu trong request RTR (không thấy trong ảnh)?
- **Q19.** Xung đột shared mailbox ↔ `Ban_Users` (§11.3): chọn (a), (b) hay (c)?
- **Q20.** Danh sách giá trị "Loại biến động" của RTR và ý nghĩa: "Chính thức" là **nhân viên mới** hay **chuyển từ thử việc
  sang chính thức** (không cần tạo tài khoản)? Có các loại Thử việc, Thực tập, Điều chuyển, Nghỉ việc…? "Điều chuyển"
  (đổi phòng → move OU, đổi group) có cần làm trong Module 1 không?
- **Q21.** Trên BioStar 2, `user_id` đang lưu `003551` hay `3551`? Phiên bản BioStar 2?
- **Q22.** Phiên bản/build SDP on-prem? Tài sản (PC, Laptop, Màn hình…) đang gán cho user theo cách nào: field "User" của asset hay qua "Assign to request"?
- **Q23.** Rancher/K8s: ingress controller nào? Chứng chỉ TLS (CA nội bộ)? StorageClass cho PVC? PostgreSQL chạy trong cluster
  (vd. CloudNativePG) hay DB server có sẵn? Container registry nào (Harbor…)? Cluster có tới được DC (636), Exchange, BioStar,
  SDP và ra internet (Graph) không?
- **Q15/Q16** (chưa trả lời): kênh thông báo (email/Teams)? Chu kỳ poll đối soát (đề xuất 30 phút), giữ snapshot 90 ngày?

---

## 12. Điều chỉnh ưu tiên (16/11/2026) & phân tích mẫu biên bản

### 12.1 Quyết định
- **Tạm hoãn** mọi tính năng/thông tin liên quan tài khoản Microsoft (Entra ID, AD, Exchange, Entra Connect).
- **Ưu tiên** kết nối **ServiceDesk Plus** và **Suprema BioStar** để lấy dữ liệu, và xây dựng **Biên bản Bàn giao / Thu hồi** theo mẫu thật.
- Đăng nhập app: dùng **tài khoản nội bộ** (`INFRAOPS_AUTH_MODE=local`) cho tới khi có App Registration; SSO Entra đã có sẵn trong code, bật bằng cấu hình.
- PR của từng phase được merge thẳng vào `main`.

### 12.2 Kế hoạch phase cập nhật

| Phase | Nội dung |
|---|---|
| 1 ✅ | Platform Core; connector SDP + BioStar (test kết nối, quyền thực tế) |
| 2 | Adapter đọc SDP (request RTR + parser mô tả, tài sản, người dùng) và BioStar (user, nhóm, access group); trang tra cứu theo Mã NV; đối soát SDP ↔ tài sản ↔ BioStar; export Excel/CSV |
| 3 | Core template engine (docxtpl + LibreOffice) + Biên bản Bàn giao / Thu hồi từ 2 mẫu thật; số biên bản; upload bản ký |
| 4 | Workflow Onboard không có bước Microsoft: gán tài sản SDP → user BioStar → đăng ký vân tay/thẻ (thủ công) → biên bản → worklog + đóng request |
| 5 | Workflow Offboard: thu hồi tài sản → In Store → vô hiệu hoá/xoá BioStar → biên bản thu hồi; lên lịch theo ngày nghỉ việc |
| sau | Bổ sung bước Microsoft vào workflow khi đã chốt |

### 12.3 Phân tích mẫu ITD-FRM-AST-001 (Bàn giao) và ITD-FRM-AST-002 (Thu hồi)

Cấu trúc chung: header (logo, tiêu đề, *Effected date*, *Document ID* `IT/OP/FRM/2026-06/01|02`, *Page*),
**Nội dung A** (thông tin + danh mục thiết bị + cam kết + ký) và **Nội dung B** (checklist + ký). Song ngữ Việt/Anh.

Placeholder đề xuất (Jinja2 trong DOCX):

| Vùng | Placeholder | Nguồn dữ liệu |
|---|---|---|
| Ngày bàn giao / thu hồi | `{{ handover_date }}` / `{{ return_date }}` | Ngày thực hiện Job |
| Người nhận / Người bàn giao | `{{ employee.full_name }}` | Request RTR (SDP) |
| Mã nhân viên | `{{ employee.id }}` | Request RTR (giữ số 0 đầu) |
| Phòng ban | `{{ employee.department }}` | RTR: Phòng / Bộ phận (mapping cấu hình được) |
| Chức danh | `{{ employee.job_title }}` | RTR: Chức danh nội bộ |
| Địa điểm làm việc | `{{ employee.work_location }}` | RTR: Nơi làm việc |
| Bảng thiết bị (dòng lặp) | `{% for a in assets %}{{ loop.index }} · {{ a.name }} · {{ a.tag }} · {{ a.date }} · {{ a.note }}{% endfor %}` | Tài sản SDP gán/thu hồi trong Job |
| Ký – Đại diện IT | `{{ it_rep.full_name }}`, `{{ signed_date }}` | Người thực hiện Job |
| Ký – Người nhận / bàn giao | `{{ employee.full_name }}` | |
| Ký – Người kiểm tra (Nội dung B) | `{{ reviewer.full_name }}` | Chọn khi lập biên bản |
| Checklist (B) | `{{ '☑' if c.ok else '☐' }}` | Để trống (đánh tay trên giấy) hoặc tick trong app (Q24) |
| Phần mềm đã cài (B – Bàn giao) | như trên | Như trên |

Cần chỉnh nhẹ trong file mẫu khi làm Phase 3: bảng thiết bị hiện cố định 4 dòng → chuyển thành 1 dòng lặp
(không giới hạn số thiết bị, tự sang trang).

### 12.4 Câu hỏi về biên bản

- **Q24.** Checklist Nội dung B (8 mục cấu hình + 10 phần mềm khi bàn giao, 3 mục khi thu hồi): để trống cho IT
  đánh dấu tay trên bản in, hay IT tick trong app rồi in sẵn kết quả?
- **Q25.** Mẫu không có ô **Số biên bản**. Có thêm số tự sinh (`BG-YYYYMM-####` / `TH-YYYYMM-####`) vào header không
  (cạnh Document ID)? Số này dùng để tra cứu và đính kèm vào request SDP.
- **Q26.** Biên bản Thu hồi không có cột **Tình trạng** (tốt / hỏng / thiếu phụ kiện) và mục tài sản **thiếu**.
  Ghi vào cột *Ghi chú*, hay thêm cột *Tình trạng* và một dòng tổng hợp "Thiết bị chưa thu hồi"?
- **Q27.** "Tên tài sản" lấy theo **Product** (vd. "Dell Latitude 5440") hay **tên tài sản** trong SDP (vd. "HSC-LT-0123")?
  "Mã tài sản" là **Asset Tag** hay **Serial**?
- **Q28.** Ai là "Người kiểm tra / Reviewer" ở Nội dung B: trưởng nhóm IT cố định, hay chọn theo từng biên bản?

---

## 13. Quyết định vòng 3 (16/11/2026) & phạm vi Phase 2

### 13.1 Câu trả lời

| # | Quyết định | Ảnh hưởng thiết kế |
|---|---|---|
| Q20 | RTR có các loại biến động: **nhân viên mới**, **chuyển từ thử việc sang chính thức**, **thuyên chuyển** | Mapping "Loại biến động" → loại sự kiện (`onboard`, `probation_to_official`, `transfer`, `offboard`) cấu hình được; giá trị lạ hiển thị để Admin gán |
| Q21 | BioStar `user_id` = Mã NV dạng chuỗi, giữ số 0 đầu (`003551`) | So khớp trực tiếp, không chuẩn hoá số |
| Q22 | SDP on-prem build **16020**; tài sản gán cho user trong màn hình Asset, user SDP đồng bộ từ AD qua LDAP | Chủ sở hữu tài sản = user SDP; nối với Mã NV qua trường `employee_id` của user SDP (cấu hình được), dự phòng theo họ tên khi duy nhất |
| Q24 | Checklist Nội dung B được **tick trong app** rồi in sẵn | Phase 3: lưu checklist theo từng biên bản |
| Q25 | **Thêm số biên bản tự sinh** | `BG-YYYYMM-####` / `TH-YYYYMM-####` |
| Q26 | Biên bản Thu hồi **thêm cột mới** (Tình trạng) | Phase 3 chỉnh mẫu: cột Tình trạng + dòng tài sản chưa thu hồi |
| Q27 | Tên tài sản = **tên tài sản trong SDP**; Mã tài sản = **Asset Tag** | |
| Q28 | Người kiểm tra (Nội dung B) là **người cố định** | Cấu hình trong Settings của module |

### 13.2 Phạm vi Phase 2
- Core: lưu cấu hình theo module (`core_module_setting`) và bộ lập lịch nền chạy trên PostgreSQL (`core_scheduled_task` + tiến trình `worker`).
- Đọc dữ liệu bằng credential **Read**: request RTR (kèm parser nội dung mô tả), user và tài sản SDP; user BioStar (nhóm, nhóm quyền ra vào, thời hạn, số thẻ/vân tay/khuôn mặt).
- Lưu snapshot mỗi lần đồng bộ (thủ công + định kỳ) để so sánh và xem lịch sử.
- Màn hình **Đối soát theo Mã NV** (SDP request · Tài sản · BioStar) với quy tắc phát hiện lệch, bộ lọc, chi tiết, export Excel/CSV.
- Màn hình **Request RTR** (đã parse, lỗi parse), **Đồng bộ dữ liệu**, **Cấu hình module**.

---

## 14. Phase 3 – ghi chú triển khai (17/11/2026)

- Mẫu Bàn giao / Thu hồi được chuyển từ file Word của công ty (giữ nguyên bố cục) bằng
  `backend/scripts/build_form_templates.py`: số biên bản dưới tiêu đề header, bảng thiết bị 1 dòng lặp
  (không giới hạn số dòng), cột **Tình trạng** trên Biên bản Thu hồi (Tốt / Hỏng / Thiếu phụ kiện / Chưa thu hồi),
  checklist Nội dung B và phần mềm in sẵn ☑ / ☐, bảng cố định độ rộng, dòng không bị tách giữa 2 trang.
- Số biên bản cấp khi **chốt** (không cấp cho bản nháp) → không có số bị bỏ trống do nháp bị huỷ.
- Mẫu dùng font **Aptos** (Microsoft). Trên container, PDF dùng font thay thế; để giống hệt bản Word cần cung cấp
  file font Aptos (Helm `fontsSecret` / thư mục `fonts` trong compose).
- **Cần kiểm tra với SDP 16020 thật**: API đính kèm file vào request. Mặc định dùng
  `POST /api/v3/requests/{request_id}/_uploads` với field `filename`; đổi được trong Cấu hình module
  nếu bản SDP dùng đường dẫn/field khác.

---

## 15. Phase 4 – Workflow Onboard / Offboard (18/11/2026)

- Yêu cầu: cấu hình API key SDP trên giao diện (đã có từ Phase 1), lấy dữ liệu Onboard/Offboard từ SDP
  (Phase 2) và **chạy quy trình** Onboard/Offboard (phase này). Bước Microsoft vẫn tạm hoãn.
- Job nháp tự tạo sau mỗi lần đồng bộ cho request loại `onboard` / `offboard` (tắt được trong Cấu hình module).
  Request loại *Thử việc → chính thức* và *Thuyên chuyển* chưa có quy trình.
- Onboard chờ user SDP xuất hiện (tài khoản AD do Helpdesk tạo, LDAP import vào SDP), kiểm tra lại mỗi 15 phút,
  tối đa 72 giờ.
- **Cần kiểm tra với hệ thống thật (nên làm trên môi trường Test):**
  - SDP 16020: `PUT /api/v3/assets/{id}` với `input_data` `{"asset":{"state":{"name":…},"user":{"id":…}}}`;
    ghi chú `POST /api/v3/requests/{id}/notes`; đổi trạng thái `PUT /api/v3/requests/{id}`; tìm user theo trường
    Mã NV; tìm tài sản theo `user.id`; đính kèm file (đường dẫn/field chỉnh trong Cấu hình module).
  - BioStar 2: `GET/POST/PUT /api/users`, `DELETE /api/users?id=…`, nhóm người dùng / nhóm quyền ra vào.

---

## 16. Phase 5 – Vai trò & module Công việc Helpdesk (23/11/2026)

**Yêu cầu:** SSO Entra ID; vai trò Manager / Operator / Viewer; đồng bộ task Helpdesk/Support trên SDP theo từng
Operator (đã thực hiện, đang chờ, quá hạn); Manager giao/chuyển task khi Operator nghỉ phép; thống kê theo tuần,
tháng, quý, 6 tháng, 1 năm hoặc theo lịch. Nguồn dữ liệu của app: SDP (task, user, tài sản của user cho offboard)
và BioStar (user ra vào). Không có nguồn Microsoft.

**Quyết định đã chốt:**

| Câu hỏi | Trả lời |
|---|---|
| Chuyển task có ghi sang SDP? | **Có** – đổi Technician của request trên SDP (credential Write), chạy trong worker, đọc trước khi ghi. |
| Nguồn vai trò khi SSO | **Gán trong app** (như Q12). Đăng nhập SSO lần đầu = Chỉ xem. SSO Entra đã có từ Phase 1 (`INFRAOPS_AUTH_MODE=oidc`). |
| Người backup khi nghỉ phép | **Chuyển thủ công**: Manager chọn nhiều task rồi chuyển hàng loạt. Không quản lý lịch nghỉ. |
| Giao diện | Hướng **B · Phòng điều khiển** (mockup `docs/design/redesign-mockup.html`) – áp dụng ở Phase 7. |

**Triển khai:**
- Vai trò `helpdesk` đổi tên thành `operator` (migration `0005_core`, giữ nguyên quyền đã chỉnh); thêm `manager`
  = quyền Operator + đóng Job bắt buộc + xem nhật ký + quyền module Công việc. Quản trị (toàn quyền) giữ nguyên.
- Module `workload` (bảng `workload_technician`, `workload_ticket`): đồng bộ tăng dần theo `last_updated_time`
  (chồng lấn 1 giờ). Trạng thái task suy từ tên trạng thái SDP theo cấu hình; quá hạn = đang mở và quá `due_by`,
  hoặc hoàn tất sau `due_by`. Thống kê nhóm theo ngày/tuần/tháng tuỳ độ dài kỳ, tính theo giờ Việt Nam.
- Chuyển việc = workflow `workload.reassign` (1 bước tự động, `before_state` lưu Technician cũ).

**Cần kiểm tra với SDP 16020 thật:**
- `GET /api/v3/technicians` (trường `email_id`), danh sách request có `technician`, `due_by_time`,
  `completed_time` (hoặc `resolved_time`); điều kiện tìm `last_updated_time greater than` và `group.name` nhiều giá trị.
- `PUT /api/v3/requests/{id}` với `{"request":{"technician":{"id":…}}}`: SDP có thể từ chối nếu technician
  không thuộc Group của request → lỗi hiện ở banner chuyển việc.

**Phase 6 (yêu cầu mới, chưa làm):** lưu đồ các bước Onboard/Offboard, checklist từng bước, mỗi bước cần Operator
kiểm tra + xác nhận hoàn tất **kèm bằng chứng** mới hợp lệ. Câu hỏi cần chốt trước khi làm: loại bằng chứng
(ảnh chụp màn hình, file PDF, ghi chú, liên kết) và bắt buộc ở mọi bước hay chỉ bước được đánh dấu; ai sửa
checklist (Manager trên giao diện hay cố định trong code); bước tự động (đã ghi qua API) có cần Operator xác nhận lại không.

**Bổ sung phân quyền (23/11/2026):**
- Operator **chỉ thấy task và thống kê của mình** (API ép bộ lọc theo technician đã liên kết; quyền
  `workload.tickets.view_all` mở xem tất cả – mặc định Manager, Supervisor, Chỉ xem).
- Vai trò mới **Supervisor** (giữa Operator và Manager): quyền Operator + xem task mọi Operator + điều phối/chuyển task.
  Không cấu hình hệ thống/module, không quản lý người dùng. Migration `0006_core` tạo vai trò cho DB đang chạy.
- Manager thêm quyền cấu hình module Vòng đời và mẫu biên bản. Kết nối hệ thống và quản lý người dùng vẫn chỉ
  Quản trị (cấp `core.rbac.manage` cho Manager sẽ cho phép tự gán vai trò Quản trị).
- Job chuyển việc chỉ hiện với người có quyền điều phối.

**Phase 6 – câu trả lời (23/11/2026):**
- Bằng chứng: ảnh chụp màn hình, file PDF/tài liệu, ghi chú, liên kết (một bước có thể nhận nhiều loại).
- Chỉ **bước được đánh dấu** trong checklist mới bắt buộc bằng chứng; bước tự động hợp lệ nhờ nhật ký API.
- **Manager sửa checklist trên giao diện** (có phiên bản + nhật ký; Job đang chạy giữ checklist lúc bắt đầu).
- Operator xác nhận là đủ, **không** cần Manager duyệt lại.

---

## 17. Phase 6 – Lưu đồ, checklist và xác nhận kèm bằng chứng (23/11/2026)

Theo câu trả lời ở §16:
- **Trạng thái bước mới `verify` (Chờ kiểm tra).** Với quy trình bật `verify_steps` (Onboard, Offboard), mỗi bước
  tự động hoặc thủ công sau khi làm xong sẽ dừng ở `verify`. Bước sau chỉ chạy khi Operator xác nhận: tick đủ mục
  checklist, và có ≥ 1 bằng chứng nếu bước được đánh dấu. *Báo không đạt* (bắt buộc lý do) chuyển bước sang lỗi →
  sửa → *Chạy lại* (bước đọc trạng thái hiện tại trước khi ghi). Bước đang chờ kiểm tra không bỏ qua được.
- **Bằng chứng:** ảnh PNG/JPG (dán ảnh chụp màn hình), PDF/DOCX (kiểm tra magic bytes, lưu qua core storage),
  liên kết http(s), ghi chú. Sửa/xoá được đến khi bước được xác nhận; sau đó cố định. Mọi thay đổi ghi nhật ký.
- **Checklist có phiên bản** theo quy trình (`core_workflow_checklist`): phiên bản 0 = mặc định trong code,
  Manager (`core.workflow.checklist.manage`) lưu phiên bản mới trên trang *Lưu đồ quy trình*. Job chụp checklist khi
  bắt đầu (`core_job.checklist_version`), Job đang chạy không bị ảnh hưởng khi sửa. Job bắt đầu trước khi nâng cấp
  không yêu cầu kiểm tra.
- **Lưu đồ:** trang *Lưu đồ quy trình* và nút *Lưu đồ* trên Job (trạng thái từng bước + checklist đã tick).
- Checklist mặc định: bước ghi SDP/BioStar và bước thu hồi/đăng ký vân tay **bắt buộc bằng chứng**; kiểm tra
  request, xác định user SDP, biên bản, đóng request chỉ cần tick checklist. Manager chỉnh lại được.

---

## 18. Phase 7 – Giao diện “Phòng điều khiển” (23/11/2026)

- Áp dụng hướng **B** đã chọn (mockup `docs/design/redesign-mockup.html`) cho toàn app; chi tiết trong
  `docs/design-system.md`.
- Khung mới: **rail phân hệ** (Tổng quan · Vòng đời · Công việc · Hệ thống ở dưới cùng) + **menu con** của phân hệ
  đang mở; màn hình hẹp hiện menu con thành thanh cuộn ngang. Thêm mục *Job onboard / offboard* vào menu
  (trước đây chỉ vào được từ request/dashboard).
- Token: slate + cobalt, `rail`, `shadow-card`, bo góc 10px cho panel, badge trạng thái dạng viên thuốc; font Inter
  (bỏ Be Vietnam Pro). Mọi cặp chữ/nền ≥ 4,5:1 và viền điều khiển/viền focus ≥ 3:1 ở cả hai giao diện (đã tính).
- Ô KPI chỉ tô màu khi số lỗi/cần chú ý > 0.
- axe-core: 0 lỗi trên 16 trang + trang đăng nhập, sáng và tối.

---

## 19. Sửa theo critique giao diện (23/11/2026)

Critique (`/impeccable critique`, 25/40) → đã sửa toàn bộ:
- **An toàn thao tác:** hộp thoại *Chuyển việc* liệt kê các request sẽ chuyển và người đang xử lý, nút ghi rõ
  "Chuyển N task cho {tên}". Xác nhận bước tự lưu liên kết/ghi chú đang gõ dở (trước đây bị mất); lỗi hiện ngay
  cạnh checklist và khung bằng chứng; *Huỷ Job* và *Buộc đóng Job* tách vào mục "Kết thúc Job sớm" kèm giải thích.
- **Ngôn ngữ:** một tên cho trạng thái chờ ("Chờ Operator kiểm tra bước N"); tiêu đề/danh sách Job nói rõ đang chờ
  gì; nhãn tiếng Việt cho mọi hành động trong Nhật ký kiểm tra; bỏ chữ hoa toàn bộ; lưu đồ định nghĩa dùng gạch
  đầu dòng thay cho ô tick.
- **Tổng quan theo vai trò:** widget *Công việc Helpdesk* (Operator: task của mình; Manager/Supervisor: quá hạn,
  chưa giao) và *Cần xử lý tiếp* cho Job; lần đầu (chưa có kết nối) hiện 4 bước bắt đầu thay cho các ô số 0.
  Màn trống Đối soát dẫn tới *Thêm kết nối* khi chưa có kết nối.
- **Điện thoại:** bước hiện tại lên trước danh sách bước, hết tràn ngang, nút *Chụp ảnh* mở camera làm bằng chứng.

**Critique lần 2 (28/40) → đã sửa:** Ctrl K (và mọi hộp thoại có ô nhập) focus đúng ô nhập; trên điện thoại rail
thành thanh tab ở đáy, thanh *Xác nhận / Báo không đạt* dính đáy, dòng checklist cao 44px, đầu trang Job có
"Bước N/T"; danh sách Job gộp cột tiến độ, chip lọc cùng kiểu trang Công việc (chip 0 làm mờ); nút *Huỷ Job* /
*Buộc đóng Job* cùng kiểu nguy hiểm; số 0 không tô màu; ticket quá hạn ghi "Trễ N ngày" (đỏ từ 7 ngày), chỉ hiện
trạng thái SDP khi khác thường; biểu đồ ghi "(0 trong kỳ)" cho chuỗi trống.

## 20. Phase 8 – Việc của tôi, người phụ trách Job, đề xuất mua thiết bị (23/11/2026)

Quyết định (theo trả lời của product owner):
- **Người phụ trách Job = technician của request RTR trên SDP.** Sync đọc technician của request và cập nhật
  `assignee` của Job (`source=sdp`). *Chuyển Job* trong app chạy workflow `lifecycle.assign` ở worker: tìm
  technician theo e-mail, đọc request, chỉ PUT khi khác, rồi ghi `core_job_assignment` (`source=app`, người
  chuyển, lý do, bước đang chạy). Quyền `lifecycle.job.assign` (manager, supervisor).
- **Tài khoản Operator tự tạo** cho technician có e-mail chưa có tài khoản (`auto_create_users`, mặc định bật;
  bỏ qua technician đã map tay hoặc trùng UPN/e-mail), ghi audit `workload.user_created` (actor `system`).
- **Trạng thái task:** `phase` = `pending` / `in_progress` / `on_hold` từ trạng thái SDP (cấu hình được);
  đổi cấu hình thì phân loại lại toàn bộ.
- **Đề xuất mua thiết bị chỉ trong app** (`workload_purchase_request`): gợi ý theo từ khoá (thay thế / nâng cấp /
  sửa chữa) trên tiêu đề + category; luồng `proposed → approved → ordered → received`, `rejected` (cần lý do) có
  thể mở lại; xuất Excel. Không ghi gì lên SDP.
- **Lưu đồ swimlane theo người** (core `Swimlane`): mỗi người phụ trách một làn, mũi tên nét đứt nhảy làn tại
  điểm chuyển giao, bước hiện tại nhấp nháy (tắt khi giảm chuyển động); bản checklist cũ nằm trong mục mở rộng.
- **Portal `/me/work`** là trang core; module khai báo `portal` section (lifecycle: Job của tôi; workload: task
  và gợi ý mua). `GET /api/core/jobs?assignee=me&workflows=…` cho danh sách Job theo người.

## 21. Phase 9 – PO tổng, bảng giá, engine kho & cấp phát (23/11/2026)

Quyết định (theo trả lời của product owner):
- **Band lấy từ request RTR** (nhãn cấu hình được, mặc định “Band”); nhập tay trên app khi thiếu/sai
  (`lifecycle_request.band_override`, audit `lifecycle.band_set`).
- **Đơn giá từ bảng giá** (`workload_catalog_item`) do Supervisor/Manager cập nhật sau khi đối tác báo giá
  (`workload.catalog.manage`). Giá đổi → PO nháp được gom lại; PO đã chốt giữ giá lúc chốt (`unit_price` đóng băng).
- **Gom PO**: chỉ đề xuất **đã duyệt**; duyệt cần ≥1 bằng chứng Function Head (`workload_purchase_evidence`, file
  qua core storage, magic-byte) và thiết bị có giá. Thuật toán first-fit theo thời điểm duyệt (`po.pack`, có unit
  test); hạng mục đơn lẻ vượt hạn mức nằm riêng một PO và được cảnh báo. `repack` chạy sau duyệt/từ chối, đổi giá,
  đổi hạn mức, dưới advisory lock. PO nháp chưa có số; số `PO-<năm>-<STT>` cấp khi chốt.
- **Chốt PO** (`workload.po.close`, Supervisor/Manager): kiểm tra tổng tiền người dùng đã thấy (`po_changed` nếu
  vừa đổi), chuyển đề xuất sang `ordered`, audit `workload.po_closed`. **Email nháp = file .eml** (`X-Unsent: 1`,
  mở bằng Outlook), mẫu tiêu đề/nội dung Jinja sandbox trong cấu hình (kiểm tra cú pháp khi lưu), đính kèm Excel
  chi tiết và bằng chứng. Không dùng Exchange (tích hợp Microsoft vẫn tạm hoãn).
- **Engine kho & cấp phát** (`lifecycle/allocation.py`, hàm thuần có test): band → tiêu chuẩn → tài sản In Store
  từ snapshot đồng bộ gần nhất; ưu tiên ngày hiệu lực sớm; tài sản đã chọn trên Job nháp được giữ. Thiếu → gợi ý
  lập đề xuất mua (liên kết sang module workload qua URL, không import chéo module).
- **Tooltip hướng dẫn**: core `HelpTip` (nút “?” mở ghi chú, chạm/bàn phím được, Esc đóng) trên các phần mới.

## 22. Phase 10 – Nhận hàng → nhập kho SDP (24/11/2026)

Product owner chọn khép vòng mua sắm của phase 9:
- Bảng giá có thêm **Sản phẩm trên SDP** (`workload_catalog_item.sdp_product`). Có giá trị → thiết bị được quản lý
  tài sản; trống → linh kiện/sửa chữa, nhận hàng chỉ đổi trạng thái.
- `ordered → received` không còn là thao tác tay: `POST /api/workload/purchases/{id}/receive` (quyền
  `workload.purchase.manage`). Thiết bị có sản phẩm SDP cần đủ serial (không trùng) = số lượng; API tạo Job
  `workload.receive` (worker). Bước ghi đọc trước theo `serial_number`, chỉ tạo tài sản còn thiếu (trạng thái
  `asset_state_in_store`, mặc định “In Store”, tên = asset tag hoặc serial), `before_state` ghi serial đã có, lỗi
  thì không đổi trạng thái đề xuất và bấm Thử lại an toàn. Xong: đề xuất `received` + `received_assets`, audit,
  và `request_run("lifecycle.sync")` để Kho & cấp phát thấy hàng mới (bỏ qua nếu module lifecycle không cài).
- Không tự gán tài sản mới cho onboard: engine cấp phát phase 9 đã gợi ý tận dụng sau khi đồng bộ; Operator vẫn
  xác nhận bằng “Chọn tài sản tận dụng vào Job”.

## 23. Phase 11 – Báo giá: 3 báo giá PC/Laptop, giá từ PDF, Operator không thấy giá (24/11/2026)

Quyết định (theo trả lời của product owner):
- **Operator chỉ đề xuất thiết bị.** Quyền mới `workload.price.view` (manager, supervisor): đơn giá, thành tiền,
  báo giá, PO (`/pos`, cột PO), Bảng giá có giá và cột giá trong Excel chỉ dành cho người có quyền này; API ẩn ở
  serializer (`_full(..., user)`), danh sách Bảng giá của Operator chỉ còn tên/nhóm/quy cách.
- **3 báo giá theo từng đề xuất**: nhóm `quote_categories` (mặc định PC, Laptop, Desktop, Workstation) được định giá
  bằng `workload_purchase_quote` (đối tác, đơn giá, file PDF magic-byte). `quote_price` = giá thấp nhất, dùng cho
  PO/gom PO. Duyệt cần ≥1 báo giá; giá thấp nhất > `quote_threshold_vnd` (20 triệu) cần ≥ `quotes_required` (3).
  Báo giá chỉ sửa khi đề xuất chưa duyệt; file đính kèm email chốt PO. Quy tắc là hàm thuần `quote_problem`.
- **Giá từ PDF** cho thiết bị khác: `POST /catalog/quote-pdf` đọc lớp chữ bằng pypdf (PDF scan → báo lỗi
  `pdf_no_text`), `match_prices` (hàm thuần, có test) khớp tên thiết bị (bỏ dấu, ≥60% từ) và lấy số tiền nhỏ nhất
  trên dòng (hoặc dòng kế tiếp). Chỉ là gợi ý: Supervisor xác nhận qua `POST /catalog/apply-quote` (file phải do
  chính người đó tải lên), giá + đối tác + ngày + file lưu vào Bảng giá, audit từng dòng, gom lại PO nháp.

## 24. Phase 12 – Nhận diện báo giá nhà cung cấp, không có hàng, email trình duyệt PO (24/11/2026)

- **Nhà cung cấp** (`workload_supplier`, quyền sửa `workload.catalog.manage`, xem `workload.price.view`): tên,
  từ khoá nhận diện (mã số thuế, tên miền…, so khớp không dấu), `price_position` (0 = số tiền nhỏ nhất trên dòng,
  N = số tiền thứ N từ trái theo mẫu của họ), website. Chỉ 4–5 nhà cung cấp nên chọn cấu hình theo mẫu thay vì
  học máy. `detect_supplier` / `pick_price` là hàm thuần có test.
- Đọc PDF: Bảng giá (`/catalog/quote-pdf`) và báo giá từng đề xuất (`/purchases/{id}/quotes/read`, gợi ý nhà cung
  cấp + đơn giá; file được lưu và dùng lại bằng `file_id` khi thêm báo giá, chỉ người tải lên mới dùng được).
- **Không có hàng**: `workload_purchase_quote.out_of_stock` + ảnh/PDF chụp website, `unit_price` rỗng. Tính vào số
  báo giá bắt buộc (`quote_problem(prices, out_of_stock, …)`), vẫn cần ≥1 báo giá có giá.
- **Email trình phê duyệt PO** (Head of IT, Finance): `.eml` có bản text và HTML; biến mới `{{ items_table }}`
  (bản HTML là bảng so sánh báo giá, giá được chọn, không có hàng – mọi giá trị người dùng đều escape); đính kèm
  thêm PDF báo giá và ảnh không có hàng. Mẫu mặc định đổi sang lời trình duyệt; hệ thống đã lưu mẫu cũ giữ nguyên
  (`items_text` cũng hiện thành bảng trong bản HTML).

## 25. Sửa theo critique lần 3 (24/11/2026)

Critique 27/40 (sau phase 8–12) → đã sửa:
- **Job đứng yên**: `job_out.stalled_minutes` (Job `running` không được worker cập nhật quá 10 phút). Trang Job báo
  “Bước tự động chưa chạy sau X – worker có thể đang dừng… không cần bỏ qua bước”, bước tự động đang chạy có
  thông báo tiến độ; badge “Đứng yên X ở bước N” ở danh sách Job, Tổng quan (“Cần xử lý tiếp”). Bộ đếm thống nhất:
  “Đang ở bước N/T · xong M”.
- **Đề xuất mua**: hộp thoại Duyệt có bối cảnh (người đề xuất, người dùng, nguồn, lý do) và cảnh báo trùng (cùng
  thiết bị + người dùng đang chờ/đã duyệt); tên người đề xuất thay email (`created_by_name`); mã PO/nguồn không
  xuống dòng; **duyệt/từ chối hàng loạt** (`POST /purchases/bulk`, từng đề xuất kiểm tra như duyệt lẻ trong
  savepoint, cái không đạt giữ nguyên và báo lý do); lý do nút Duyệt bị khoá gắn `aria-describedby`; dạng thẻ trên
  điện thoại.
- **Ghi ra ngoài**: Nhập kho SDP nêu kết nối đích (`GET /purchases/receive-target`), PO, “không tự xoá được”;
  Enter chuyển sang serial kế tiếp (máy quét mã vạch); Chốt PO hiện người nhận email trình duyệt.
- **Tổng quan**: thẻ bắt đầu không còn nói “số liệu trống”; “Đang mở” cùng định nghĩa với trang Job; số 0 trung
  tính; “0 nhân viên” là cảnh báo.
- Điện thoại: menu con tự cuộn tới mục đang chọn, tab không xuống dòng, trang Kho đưa nhân viên lên trước KPI.
- Câu chữ: Việc của tôi “Job đang giao cho bạn phụ trách”, gợi ý mua có HelpTip “Không cần”, mô tả trang Đề xuất
  mua rút gọn, thanh hạn mức PO ≥90% cho biết đề xuất nào sẽ sang PO mới.
- (Phát hiện “Job chuyển giao không hiện ở Việc của tôi” là lỗi dữ liệu demo sửa tay bằng SQL, không phải lỗi.)

## 26. Phase 13 – Hướng tối giản: làm từng bước (24/11/2026)

Product owner chọn hướng **wizard tối giản cho Operator** (app cung cấp thông tin chính xác, Operator xác nhận thay vì
nhập), triển khai lần lượt: Onboard/Offboard (phase 13) → đề xuất mua (14) → “Việc tiếp theo” (15) → nhận hàng (16).
Quyết định:
- Wizard cho Operator, màn đầy đủ cho Supervisor/Manager (giám sát, ngoại lệ); chuyển qua lại bằng `?view=`.
  Mặc định theo quyền `lifecycle.job.assign` (người điều phối).
- **Điền sẵn, Operator bấm xác nhận** – không bước ghi nào chạy mà chưa có người xem. Mỗi giá trị biết trước có
  nhãn nguồn (“khớp Mã NV …”, “gợi ý từ kho”).
- Core `Wizard` / `WizardProgress` (`core/ui/wizard.tsx`): một câu hỏi mỗi màn, một nút chính, focus vào tiêu đề
  khi đổi bước, lý do nút bị khoá gắn `aria-describedby`, thanh hành động dính đáy trên điện thoại; quy trình > 5
  bước hiển thị “Bước N/M · tiếp theo: …”.
- `GET /api/lifecycle/jobs/{id}/prepare`: thông tin RTR, user SDP khớp Mã NV (snapshot), kế hoạch cấp phát của
  request (`build_plan` dùng chung với Kho & cấp phát).

## 27. Phase 14 – Đề xuất mua từng bước (24/11/2026)

- Trang `/workload/purchases/new` (core `Wizard`) thay hộp thoại đề xuất: Thiết bị → Người dùng & lý do → Bằng chứng
  Function Head → Xem lại & gửi; màn “Đã gửi” chỉ việc tiếp theo. Không có giá (Supervisor định giá khi duyệt).
- Điền sẵn qua URL (không import chéo module): `ticket`, `ref`, `item` (tiêu đề request), `kind`, `employee`
  (người yêu cầu), `reason`; từ Kho & cấp phát: `source=onboard`, `ref`, `item`, `catalog`, `qty`, `employee`.
  Link cũ `?new=1&…` chuyển hướng sang trang mới.
- Bằng chứng: nút chính bị khoá đến khi có file (lý do gắn vào nút); lối phụ “Chưa có – bổ sung sau” vẫn gửi được
  và màn xem lại/đã gửi nhắc bổ sung. Hàm hỗ trợ bằng chứng chuyển sang `workload/evidence.tsx`.
- Sửa lỗi: danh sách file đọc sau khi đã xoá giá trị input (file chọn bị mất) – sao chép trước khi xoá.

## 28. Phase 15 – Việc tiếp theo (24/11/2026)

- Hook mới trong manifest `NextActionSource(key, permission, load(session, user))` – cùng kiểu dashboard widget,
  core không import module. `GET /api/core/me/next` gộp mọi nguồn người dùng có quyền, sắp theo `priority`
  (0 làm ngay · 1 quá hạn/gấp · 2 sắp đến hạn · 3 cần biết · 4 gợi ý) rồi theo hạn; nguồn lỗi được bỏ qua và báo tên.
- Lifecycle (`next_actions.job_item`, hàm thuần có test): Job giao cho người đó – bước chờ kiểm tra / nhập Mã NV /
  thủ công và bước lỗi (0), nháp cần chuẩn bị (1 nếu hiệu lực ≤ 3 ngày, 2 nếu xa hơn), đứng yên (3); link mở wizard.
- Workload: task quá hạn (1) / đến hạn trong ngày (2), đề xuất của mình chưa có bằng chứng (2, mở thẳng đề xuất qua
  `?open=`), gợi ý mua (4, mở wizard điền sẵn); Supervisor thấy một mục “N đề xuất chờ duyệt” (1).
- Việc của tôi: thẻ “Việc tiếp theo” + nút *Làm việc tiếp theo*, danh sách theo nhóm ưu tiên, nhóm cũ trong
  “Xem theo nhóm”; wizard Job và màn “Đã gửi” đề xuất có lối “Việc tiếp theo” quay lại hàng chờ.

## 29. Phase 16 – Nhận hàng từng bước (24/11/2026)

- Wizard `/workload/purchases/receive` (quyền `workload.purchase.manage`): Chọn PO (PO đã chốt còn hạng mục đã đặt
  chưa nhận) → Serial từng máy (Enter sang ô kế tiếp, kiểm tra thiếu/trùng trong cả PO) → Xác nhận (số tài sản,
  kết nối SDP, không tự xoá được, đồng bộ Kho sau đó) → Kết quả (theo dõi Job nhận hàng, Job onboard còn thiếu máy).
- Mỗi hạng mục vẫn gọi `POST /purchases/{id}/receive` (workflow `workload.receive` – idempotent, giữ serial đã có).
  Tab PO có nút *Nhận hàng* mở wizard đúng PO; dialog nhận hàng cũ giữ cho màn Supervisor.
- Việc tiếp theo: “N hạng mục đã đặt mua chờ nhận hàng” (ưu tiên 3) cho người có quyền quản lý đề xuất.
- Core `Field` giữ `id` do control tự đặt (để chuyển focus), chỉ sinh id khi không có.

## 30. Phase 17 – Tổng quan, nhận hàng nhanh, critique luồng Operator (24/11/2026)

- Critique luồng Operator (Việc của tôi, Job wizard, Đề xuất mua, Nhận hàng): 24/40. PO chọn sửa: hàng chờ ưu tiên
  Job, thiếu máy → lập đề xuất, bước Job đang chạy, đề xuất điền sẵn; Operator được nhận hàng; mã tài sản năm 4 số.
- **Tổng quan** đầu Việc của tôi: `NextActionSource.summary` (tuỳ chọn) trả `{key, group, label, count, href}`,
  `GET /api/core/me/summary` gộp theo nhóm trễ hạn / đến hạn 3 ngày / đang mở / đã xong 7 ngày (task SDP + Job).
- Hàng chờ: task SDP quá hạn / đến hạn trong ngày gộp thành **một** mục mỗi nhóm (không có hạn → xếp sau Job cùng mức).
- Nhận hàng: quyền mới `workload.purchase.receive` (Operator, Supervisor, Manager); `GET /purchases/receivable` – PO đã
  chốt còn hàng chờ nhận, **không có giá**. `POST /purchases/receive/extract` đọc serial từ biên bản bàn giao: Excel
  (cột tiêu đề Serial/S/N/Số serial, kèm cột Asset tag/Mã tài sản), PDF có lớp chữ (nhãn “S/N:” hoặc bảng có cột
  Serial – token lặp nhiều dòng là model, bị bỏ). File chỉ đọc, không lưu; serial điền vào ô trống, khớp theo tên thiết bị.
- Mã tài sản gợi ý `<prefix>-<năm>-<mã loại>-<4 ký tự cuối serial>` (mặc định `HCM-2026-LT-1234`); prefix và bảng mã
  theo nhóm bảng giá trong cấu hình (`asset_tag_prefix`, `asset_tag_codes`, nhóm khác lấy chữ cái đầu); sửa tay thì
  không bị ghi đè; mã trùng trong PO bị chặn. Serial đang nhập được giữ khi tải lại trang (sessionStorage).
- Job wizard: dòng “Bạn cần: …” / “Không cần làm gì …” thay trạng thái; bước chưa chạy không hiện checklist, đứng yên
  không hiện “Bỏ qua bước”; thiếu thiết bị → nút *Lập đề xuất mua* điền sẵn (hiện “Đã đề xuất” khi có), gửi xong quay lại Job.
- Đề xuất từ request: tiêu đề request hiện làm ngữ cảnh, thiết bị chỉ điền khi khớp rõ bảng giá; `Wizard` focus ô
  `[data-autofocus]` của bước nếu có.

## 31. Phase 18 – Đăng nhập khi chưa có HTTPS (25/11/2026)

- Sự cố khi triển khai: tạo admin xong đăng nhập không được – `POST /auth/login` trả 204 nhưng `GET /me` vẫn 401 vì
  trang mở bằng `http://<ip>:8080`, cookie phiên có cờ `Secure` nên trình duyệt bỏ.
- `GET /api/core/auth/config` trả thêm `cookie_secure`; trang đăng nhập (trên http://, trừ localhost) hiện cảnh báo
  *Cần mở trang bằng HTTPS* kèm liên kết https://, và sau khi đăng nhập kiểm tra lại `/me` để báo lỗi rõ ràng thay vì
  quay về trang đăng nhập. Không đổi cơ chế bảo mật: môi trường thật vẫn cần HTTPS; chỉ khi thử nghiệm mới đặt
  `INFRAOPS_SESSION_COOKIE_SECURE=false`.
- Cổng mặc định của docker compose đổi từ 8080 sang **80 (HTTP) / 443 (HTTPS)**: container `web` tự phục vụ HTTPS khi có
  `./certs/tls.crt` + `tls.key` (script `docker-tls.sh` lúc khởi động bật `listen 8443 ssl` và chuyển HTTP sang HTTPS),
  không cần nginx trên máy chủ; không có chứng chỉ thì chỉ HTTP. Kubernetes không đổi (ingress lo TLS, không gắn
  chứng chỉ). Máy chủ đã cài theo cách cũ: sửa `INFRAOPS_HTTP_BIND` trong `.env` (bỏ `8080` hoặc giữ
  `127.0.0.1:8080` nếu vẫn dùng nginx máy chủ) và thêm `INFRAOPS_HTTPS_BIND`.

## 32. Audit toàn repo và sửa lỗi (25/11/2026)

- Audit (ponytail-audit) 3 phần: core (auth, kết nối, triển khai), Công việc Helpdesk (đề xuất, PO, nhận hàng),
  workflow + Vòng đời nhân sự. Đã sửa toàn bộ 28 phát hiện, mỗi phát hiện có test.
- Đáng chú ý: worker gắn volume `files` (bước đóng request đọc được biên bản đã ký); pump workflow không còn giới hạn
  20 Job (Job mới không bị kẹt sau Job chờ người); tổng quan Việc của tôi không lỗi khi có Job hoàn tất; PO không chốt
  được khi còn hạng mục chưa có giá và phải đúng danh sách hạng mục đã xem; Operator chạy lại được Job nhận hàng;
  serial không dùng cho hai đề xuất; đổi máy chủ của kết nối thì xoá credential; SSO không chiếm tài khoản đã gắn
  danh tính Microsoft khác; khoá tài khoản đếm đúng khi đăng nhập sai song song; đổi mật khẩu đăng xuất các phiên khác;
  IP trong nhật ký không giả mạo được; đóng request SDP không đính kèm/ghi chú trùng khi chạy lại; dữ liệu thô của
  đồng bộ chỉ giữ cho vài lần gần nhất.

## 33. API key SDP cá nhân (25/11/2026)

- Yêu cầu: quyền đọc/ghi SDP khi thực hiện onboard, offboard, cấp phát/nhận hàng, chuyển việc phải do tài khoản của
  chính người thực hiện chịu trách nhiệm (SDP ghi nhận đúng người), không dùng key Write chung.
- Quyết định (đã hỏi chủ sản phẩm): thiếu key thì **chặn** – bước chờ, không dùng tạm key chung; phạm vi: Job
  onboard/offboard (đọc + ghi, theo người phụ trách Job, chưa giao thì người tạo), nhận hàng (người bấm), chuyển việc
  (Manager/Supervisor bấm), đính kèm biên bản đã ký (người bấm); đồng bộ nền giữ credential Read chung.
- Core: bảng `core_user_credential` (mã hoá AES-GCM, AAD gắn user + kết nối), `open_connection(..., as_user=)`,
  `PersonalCredentialMissing` (engine chuyển bước sang chờ 2 phút/lần), API `/api/core/me/credentials` (không bao giờ
  trả secret; kiểm tra dùng `test_connection` – không ghi dữ liệu), audit đặt/xoá/kiểm tra key. Connector khai báo
  `personal_credentials` (SDP).
- UI: trang **Khoá API của tôi** (menu tài khoản), "Việc của tôi" có việc "Nhập API key cá nhân" khi người có quyền
  thực hiện Job/nhận hàng/chuyển việc chưa có key.
- Tiếp theo: trang tồn kho mới (Mới / Thu hồi / Chờ thanh lý theo state SDP, thanh lý = state Disposed, chỉ Manager và
  Administrator được ghi Disposed lên SDP).

## 34. Tồn kho: mới / thu hồi / chờ thanh lý (25/11/2026)

- Yêu cầu: trang quản lý tồn kho trực quan; phân biệt hàng mới, hàng thu hồi, hàng chờ thanh lý; mã tài sản và serial
  bắt buộc; chọn nhanh / chọn nhiều để chuyển chờ thanh lý và loại khỏi quản lý tài sản.
- Quyết định (chủ sản phẩm): trạng thái theo **state SDP** (In Store, state "Chờ thanh lý" cấu hình được); "loại bỏ" =
  đổi state sang **Disposed** (không xoá asset); chỉ **Manager / Administrator** được ghi Disposed lên SDP
  (`lifecycle.stock.dispose`); Manager / Supervisor chuyển chờ thanh lý, trả về kho, đánh dấu thu hồi
  (`lifecycle.stock.manage`).
- SDP chỉ lưu người dùng hiện tại, nên "thu hồi" được ghi trong infra-ops (`lifecycle_asset_return`): đồng bộ so với lần
  trước (từng gán → nay In Store), hoặc đánh dấu tay; xoá khi tài sản được gán lại hoặc đã thanh lý.
- Đổi state chạy trong worker (Job `lifecycle.asset_state` / `lifecycle.asset_dispose`, đọc trước khi ghi, chỉ đổi state –
  không đụng người dùng), bằng API key cá nhân của người yêu cầu (§33); thanh lý chỉ nhận tài sản đang Chờ thanh lý.

## 35. Gộp "Kho & cấp phát" và "Tồn kho" thành "Kho thiết bị" (25/11/2026)

- Vấn đề (critique): hai trang cùng đọc tồn kho In Store, hai cách đếm, hai danh sách tài sản; người dùng phải đoán
  trang nào để chuẩn bị máy cho nhân viên mới và trang nào để thanh lý.
- Quyết định (chủ sản phẩm): một mục menu **Kho thiết bị** (`/lifecycle/stock`) với ô tổng theo loại và hai tab
  *Nhân viên mới* / *Tài sản trong kho*; `/lifecycle/inventory` chuyển hướng sang tab *Nhân viên mới*. Không đổi API.
- Cấp phát gợi ý **hàng thu hồi trước hàng mới** (`allocation.returned_first`, có unit test) để dùng lại thiết bị cũ
  trước khi khui hàng mới.
- Sửa kèm: nhóm nhân viên theo ngày bắt đầu (quá hạn nổi lên đầu), chip "Lấy từ kho" dẫn thẳng tới tài sản, tiến độ Job
  đổi state hiện ngay trên dòng tài sản, xác nhận trước khi chuyển Chờ thanh lý, danh sách dạng thẻ trên điện thoại.

## 36. Kho thiết bị: chuẩn hoá tên trạng thái (25/11/2026)

- Vấn đề (critique 26/40): "Còn trống / Đang giữ / Cần mua" khó hiểu – trộn trạng thái SDP, nguồn và phân bổ; "Đang giữ"
  gồm cả gợi ý chưa khoá; "Cần mua 0 … không cần" trong khi nhân viên chưa có band không được tính.
- Quyết định (chủ sản phẩm): ô theo loại dùng 3 nhãn **Sẵn sàng cấp / Đã phân bổ / Thiếu** kèm "N trong kho"
  (Trong kho = Sẵn sàng cấp + Đã phân bổ, đúng tuyệt đối: chỉ đếm tài sản còn trong kho); Đã phân bổ tách
  **Đã chọn cho X** (trên Job nháp) và **Dự kiến cho X** (gợi ý). "Thiếu" chỉ dùng cho số lượng; thiếu dữ liệu là
  "Chưa có …". Bộ lọc tách hai nhóm: Phân bổ (cộng lại bằng Tất cả) và Nguồn (Mới / Thu hồi). "Trả về kho" → "Bỏ chờ
  thanh lý". Trang ghi rõ số nhân viên chưa có band (chưa tính vào Thiếu). Link cũ `status=returned|missing` vẫn chạy.

## 37. SDP là nguồn chính của đối soát nhân sự (26/11/2026)

- Yêu cầu (chủ sản phẩm): thông tin chính lấy từ SDP (chuẩn xác, đầy đủ); BioStar chỉ bổ sung và đối soát.
- Quyết định: danh sách nhân viên = user SDP ∪ request RTR (bỏ user nghỉ việc trên BioStar như trước); họ tên, phòng
  ban theo thứ tự SDP → RTR → BioStar. User BioStar không khớp SDP/RTR thành ghi chú `biostar_unknown_employee`
  (Mã NV, tên, còn quyền ra vào hay không, nhóm, nguồn), hiện ở mục riêng "BioStar không có trên SDP", không thành
  dòng nhân viên. Khi không đọc được SDP thì không phán xét: user BioStar vẫn hiện như trước.
- Kèm theo: tra thêm user SDP theo Mã NV khi danh sách bỏ sót, phân trang SDP theo id (§ trước), nhóm BioStar loại trừ
  khỏi đối soát (vendor…).

## 38. Job thuyên chuyển / đổi cấp trên (26/11/2026)

- Yêu cầu: đổi cấp trên trực tiếp hoặc thuyên chuyển bộ phận thì IT gỡ user khỏi nhóm AD cũ và thêm vào nhóm mới
  (quyền thư mục share của phòng ban, nhóm email).
- Quyết định (chủ sản phẩm): Job thủ công `lifecycle.transfer` – kiểm tra request, xác định user SDP, *Gỡ khỏi nhóm
  cũ*, *Thêm vào nhóm mới* (manual, checklist + bằng chứng, hiện "Từ: phòng cũ (SDP) → Đến: phòng mới (RTR)"), đóng
  request SDP. Không tự động sửa AD/Exchange (tích hợp Microsoft vẫn hoãn). RTR "Thay đổi cấp trên trực tiếp" phân
  loại là Thuyên chuyển; Job nháp được tạo tự động như onboard/offboard.

## 39. Người dùng do Administrator quản lý; chọn nhóm BioStar loại trừ (26/11/2026)

- Yêu cầu: "Người dùng & quyền" chỉ gồm người Administrator thêm hoặc xác nhận, không phải mọi technician SDP;
  Administrator được xoá tài khoản; chọn nhóm BioStar loại trừ từ danh sách đọc từ BioStar 2.
- Quyết định: bỏ tự tạo tài khoản Operator khi đồng bộ (`auto_create_users` bỏ khỏi cấu hình) bằng
  nút **Tạo tài khoản** trên từng technician (`POST /api/workload/technicians/{id}/account`, audit). Xoá tài khoản:
  `DELETE /api/core/users/{id}` (quyền `core.rbac.manage` – mặc định chỉ Administrator; không tự xoá mình; luôn còn ít
  nhất một admin; audit `rbac.user_deleted`; vai trò, phiên, API key cá nhân xoá theo; Job/biên bản/nhật ký giữ tên).
  Không tự động xoá tài khoản cũ – Administrator tự chọn.
- Nhóm loại trừ / nghỉ việc: `GET /api/lifecycle/biostar/user-groups` đọc nhóm người dùng trực tiếp từ mọi BioStar
  (chính + đọc thêm) kèm số user của lần đồng bộ gần nhất; hệ thống nào không đọc được thì dùng nhóm của lần đồng bộ.


## 40. Gộp nhóm loại trừ BioStar; báo cáo chấm công (26/11/2026)

- Yêu cầu: gộp *Nhóm nghỉ việc* và *Nhóm loại trừ khỏi đối soát* thành một mục; xuất báo cáo chấm công khi HR yêu cầu
  rà soát chấm công / Overtime (danh sách Mã NV + khung thời gian).
- Quyết định: chỉ còn `biostar_excluded_groups`. User trong nhóm được đọc nhưng coi như không còn quyền ra vào (ẩn,
  không báo “còn hoạt động” / “không có trên SDP”; vẫn hiện nếu còn request RTR, nhãn *Nhóm loại trừ*). Cấu hình cũ
  `biostar_retired_groups` được gộp tự động khi đọc.
- Chấm công: `POST /api/lifecycle/attendance/export` (quyền `lifecycle.attendance.export`: Manager, Supervisor,
  Operator; audit `lifecycle.attendance_exported`). Đọc trực tiếp mọi BioStar bằng credential Read: user khớp Mã NV
  theo 5 số cuối, `POST /api/events/search` (BETWEEN theo giờ Việt Nam, IN user), chỉ tính quẹt thành công (mã sự
  kiện 4096–4351, 4864–5119). Giới hạn 500 Mã NV, 62 ngày mỗi lần. Giờ vào/ra = lần đầu/cuối trong ngày; ca qua đêm
  chưa gộp (chờ quy tắc ca từ HR). Chạy trong request vì là thao tác đọc theo yêu cầu, không ghi gì.

## 41. Rà soát nhân sự nghỉ việc hằng tháng gửi HR (ISO 27001) (26/11/2026)

- Yêu cầu: thống kê nhân sự nghỉ việc trong tháng và soạn email gửi HR rà soát theo quy trình ISO 27001 định kỳ.
- Quyết định: trang *Rà soát nghỉ việc (ISO)* trong module Vòng đời nhân sự (quyền `lifecycle.offboard_review.export`:
  Manager, Supervisor). Danh sách = request RTR `offboard` có ngày hiệu lực trong tháng; mỗi người kèm Job offboard mới
  nhất, số tài sản đang dùng và trạng thái BioStar của lần đối soát gần nhất; kết luận *Đã thu hồi đủ* khi Job hoàn
  tất, 0 tài sản và BioStar không còn hoạt động (Annex A 5.18 / 6.5).
- `GET /api/lifecycle/offboard-review?month=yyyy-MM` (mặc định tháng trước) và `/export?format=eml|xlsx`: `.eml` nháp
  (`X-Unsent`) tới `hr_review_to` / `hr_review_cc`, nội dung đề nghị HR xác nhận danh sách đầy đủ, đúng, kèm Excel có
  cột *HR xác nhận* / *Ghi chú HR*. Mỗi lần xuất ghi audit `lifecycle.offboard_review_exported` (target = tháng).
- Nhắc định kỳ: nguồn “Việc tiếp theo” `lifecycle.offboard_review` hiện mục cho tháng trước đến khi có người tải email
  nháp của tháng đó. App không tự gửi email (chưa có SMTP; tích hợp Exchange vẫn hoãn).

## 42. Critique module Vòng đời nhân sự – sửa 5 vấn đề (26/11/2026)

- Critique (26/40): menu theo nguồn dữ liệu, nút chính trang Job ngược quy trình từng bước, Rà soát ISO cho gửi
  danh sách chưa xong, lỗi kỹ thuật thô, bảng trên điện thoại giấu cột quyết định.
- Quyết định (chủ sản phẩm: sửa cả 5, khoá BioStar đầu tiên):
  - `NavItem.group` (core) – sub-nav có tiêu đề nhóm: Việc cần làm / Hồ sơ & báo cáo / Dữ liệu nguồn / Quy trình &
    cấu hình; module mở vào *Job onboard / offboard*. Nút lọc trạng thái có 0 Job bị ẩn.
  - Trang Job: một nút chính (*Nhận Job* khi chưa có người phụ trách – `POST /api/lifecycle/jobs/{id}/claim`, quyền
    execute, cùng đường đổi Technician trên SDP như chuyển Job; sau đó *Làm từng bước*); Lưu đồ, Xem trước, Lên lịch,
    *Bắt đầu ngay…* (hộp xác nhận) vào menu *Thêm*. Điện thoại: bước hiện tại → danh sách bước → nhật ký.
  - Offboard: bước *Vô hiệu hoá / xoá user BioStar* chạy ngay sau kiểm tra request, trước thu hồi tài sản. Job đã
    tạo giữ thứ tự cũ.
  - Rà soát ISO: cảnh báo khi còn ca chưa xong / chưa có người nhận; email là nút chính chỉ khi sẵn sàng; chip
    trạng thái; liên kết Mở/Tạo Job offboard; trạng thái SDP tiếng Việt (cả trong Excel/email).
  - Lỗi: Đồng bộ hiện một dòng mỗi nguồn lỗi (chi tiết khi mở), gợi ý worker chỉ cho admin; Request RTR hiện lý do lỗi
    đầu tiên ngay trên dòng.
  - `DataTable.card` (core): dưới 640px mỗi dòng là một thẻ (Đối soát, Job).

## 43. Hàng đợi request RTR của team (26/11/2026)

- Yêu cầu: chia request RTR công bằng cho 3 Technician; ai nghỉ thì Supervisor/Manager điều phối.
- Quyết định (chủ sản phẩm): **không tự chia**. Widget *Hàng đợi request RTR của team* trên trang Tổng quan
  (`lifecycle_team_queue`, quyền `lifecycle.job.execute`): *Chờ nhận* (Job onboard/offboard/thuyên chuyển chưa có
  người phụ trách; offboard quá hạn xếp đầu, nhãn *Trễ N ngày*) với nút **Nhận**, và *Đang xử lý* theo từng người (ít
  việc trước) kèm bước hiện tại. Nhận = `POST /api/lifecycle/jobs/{id}/claim`: khoá dòng Job, từ chối khi đã có người
  phụ trách hoặc đang có lệnh giao chưa xong (`job_being_assigned`), rồi ghi Technician lên SDP bằng khoá API của người
  nhận (job `lifecycle.assign`, như *Chuyển Job*). Job đã nhận hiện ở *Việc của tôi*. Điều phối khi có người nghỉ:
  *Chuyển Job* (quyền `lifecycle.job.assign`).

## 44. Báo cáo chấm công: nhập file / dán bảng từ email (26/11/2026)

- Yêu cầu: nhập file Excel hoặc dán bảng dữ liệu trong email, nhận diện hàng loạt rồi chạy báo cáo.
- Quyết định: `POST /api/lifecycle/attendance/parse` (multipart `file` .xlsx/.xlsm/.csv/.txt ≤ 5 MB, hoặc `text`; không
  lưu gì) → `{items: [{employee_id, name}], total, skipped, column}`. Bộ nhận diện thuần (`attendance_input.py`): tìm dòng
  tiêu đề trong 10 dòng đầu (Mã NV, MSNV, Mã (số) nhân viên, Employee ID…) và cột họ tên; không có tiêu đề thì lấy mọi ô
  4–8 chữ số (STT, ngày, số điện thoại không bị nhầm); mã số ngắn hơn 6 chữ số được bù 0 đầu; đọc mọi sheet. File `.xls`
  cũ: báo lưu lại dạng .xlsx. Xuất báo cáo dùng cùng bộ nhận diện cho nội dung ô nhập. Giao diện: dán bảng (có tab /
  chữ) được gửi nhận diện ngay, ô chỉ còn danh sách mã, kèm bản xem trước Mã NV · họ tên.

## 45. Infra Handbook (26/11/2026)

- Yêu cầu: sổ tay quy định và hướng dẫn cho IT Support, dạng lưu đồ rẽ nhánh mindmap, mỗi bước có mô tả/ghi chú,
  để Manager tự xây dựng. Tên hiển thị *Infra Handbook* (rail: *Handbook*).
- Quyết định (chủ sản phẩm): chỉ sơ đồ mindmap (không có chế độ đi từng bước); ghi chú = văn bản + liên kết + ảnh;
  Manager xuất bản, có phiên bản; cấu trúc cây + nhánh nhảy tới bước có sẵn.
- Module mới `handbook` (nhánh Alembic riêng): `handbook_guide` (bản nháp JSONB), `handbook_guide_version` (bản xuất
  bản bất biến), `handbook_image` (ảnh trong core storage, PNG/JPEG kiểm tra magic bytes). Quyền: `handbook.guides.view`
  (mọi vai trò), `.edit` (Manager, Supervisor), `.publish` (Manager: xuất bản, xoá). Lưu nháp có kiểm tra xung đột
  (`stale_draft`); xuất bản từ chối khi có bước không nối với gốc. Ghi chú là markdown nhẹ, hiển thị bằng React (không
  HTML thô; liên kết chỉ http(s), mailto, đường dẫn trong app; ảnh chỉ từ kho Handbook hoặc https).
- Sơ đồ: bố cục trái → phải, mỗi bước vẽ một lần dưới nhánh tới nó sớm nhất (BFS), nhánh khác là ô “Đi tới …”.

## 46. Đổi tên thành Infra Playbook; hướng nâng cấp kiểm tra yêu cầu theo policy (26/11/2026)

- Tên hiển thị *Infra Handbook* → **Infra Playbook** (rail: *Playbook*): hợp cả hướng dẫn dạng sơ đồ lẫn hướng nâng
  cấp sau. Mã nội bộ giữ nguyên (`handbook`: module, quyền `handbook.guides.*`, bảng, URL `/handbook`) để không phải
  đổi dữ liệu và phân quyền đã cấp.
- Hướng nâng cấp (chưa làm): thêm *bước kiểm tra* (điều kiện không cần code trên dữ liệu do module khai báo: band, tài
  sản đang dùng theo loại, tiêu chuẩn cấp phát, đề xuất mua đang mở…) và *bước kết luận* (Tiêu chuẩn / Adhoc /
  Exception → cấp duyệt, luồng xử lý). Giai đoạn 1: chạy thử theo Mã NV kèm vết từng điều kiện; 2: gắn với nhóm
  request SDP, tự kiểm tra khi đồng bộ; 3: kết luận mở đúng luồng (đề xuất mua, Job, sổ ngoại lệ). Chỉ đọc dữ liệu,
  không tự từ chối, thiếu dữ liệu thì báo “Chưa đủ dữ liệu”.

## 47. Mã nội bộ handbook → playbook (26/11/2026)

- Chuẩn hoá theo tên hiển thị: module `playbook`, quyền `playbook.guides.*`, bảng `playbook_*`, API `/api/playbook`,
  trang `/playbook` (đường dẫn `/handbook…` cũ tự chuyển hướng). Migration `0002_playbook` đổi tên bảng/khoá/chỉ mục,
  sửa link ảnh trong sơ đồ đã lưu và chuyển quyền đã cấp cho vai trò – không mất dữ liệu. `INFRAOPS_ENABLED_MODULES` cũ
  có `handbook` vẫn hiểu là `playbook`. Nhật ký cũ giữ tên hành động `handbook.*`.

## 48. Tái thiết kế xoay quanh Playbook (26/11/2026)

- Yêu cầu: giao diện tối giản, mọi thứ xoay quanh Playbook; chỉ Administrator / Manager / Supervisor được thiết kế
  (thêm, sửa, thay thế, xoá) Playbook. Chủ sản phẩm chọn: Playbook là cửa vào duy nhất; trắng sáng, một màu nhấn; làm
  một lượt; các việc dở (sửa critique trang Vòng đời, Playbook giai đoạn 1) gộp vào thiết kế mới.
- Quyết định:
  - Trang chính `/` = module có `home` (Playbook): “Bạn cần xử lý việc gì?”, tìm tình huống, danh sách theo nhóm, “Đang
    chờ bạn” và hàng đợi RTR của team. Tổng quan số liệu cũ ở `/overview`. Thanh trái: Playbook · Việc của tôi ·
    Công cụ (`/tools`, mọi trang module gom theo module) · Hệ thống.
  - Bước Playbook có loại: *hướng dẫn*, *kiểm tra* (điều kiện: dữ liệu hoặc giá trị, so sánh ≥ > ≤ < bằng khác; nhánh 1
    Đúng, nhánh 2 Sai), *kết luận* (Tiêu chuẩn / Adhoc / Exception + người duyệt); mỗi bước mở tối đa 6 *công cụ* (khoá
    = đường dẫn trang). Dữ liệu cho bước kiểm tra do module khai báo (`ModuleManifest.facts`): Vòng đời nhân sự cung
    cấp band, phòng ban, số tài sản đang dùng theo loại, số lượng theo tiêu chuẩn band – đọc từ lần đối soát gần nhất.
  - `POST /api/playbook/guides/{id}/run`: đi theo sơ đồ cho một Mã NV, kiểm tra tự quyết kèm giá trị và nguồn; dừng ở
    bước cần người chọn hoặc thiếu dữ liệu (“Chưa đủ dữ liệu”, không đoán). Chỉ đọc.
  - Quyền `playbook.guides.publish` (xuất bản, xoá) thêm Supervisor (migration `0003_playbook` cho bản đã cài).
  - Sửa lỗi khởi động sau khi đổi tên quyền: migration `0002_playbook` đổi cả `core_permission`; đồng bộ quyền lúc khởi
    động bỏ qua quyền đã được cấp.
  - Màu: nền trắng, đường kẻ mảnh, bóng gần như không có; cobalt là màu nhấn duy nhất.
