# infra-ops

Nền tảng web nội bộ cho các tác vụ vận hành của team IT Infrastructure.
Kiến trúc **Platform Core + Modules**: core dùng chung (đăng nhập, phân quyền, kết nối hệ thống,
nhật ký kiểm tra, dashboard) và các module cắm vào (Module 1: Vòng đời nhân sự – Onboard/Offboard).

| Thành phần | Công nghệ |
|---|---|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 16 |
| Frontend | React 18, Vite, TypeScript, Tailwind CSS 4, TanStack Query/Table |
| Triển khai | Docker, docker-compose, Helm (Kubernetes / Rancher) |

Tài liệu: [kiến trúc](docs/architecture.md) · [thêm module](docs/adding-a-module.md) ·
[design system](docs/design-system.md) · [đề xuất & quyết định Phase 0](docs/phase-0-proposal.md)

## Trạng thái

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 0 | Hiện trạng, kiến trúc, câu hỏi mở | Xong |
| 1 | Platform Core: module, đăng nhập, RBAC, kết nối hệ thống (SDP, BioStar), audit, design system | Xong |
| 2 | SDP + BioStar: đọc request RTR, user, tài sản, user BioStar; đối soát theo Mã NV; export; bộ lập lịch nền | Xong |
| 3 | Biên bản Bàn giao / Thu hồi theo mẫu thật: mẫu có phiên bản, số tự sinh, PDF, tải bản ký, đính kèm SDP | **Xong** |
| 4–5 | Workflow Onboard / Offboard (SDP + BioStar) | |
| sau | Kết nối Microsoft (Entra ID, AD, Exchange hybrid) | Tạm hoãn |

## Chạy local

Yêu cầu: Python 3.13 + [uv](https://docs.astral.sh/uv/), Node 22, PostgreSQL 16, LibreOffice (xuất PDF biên bản).

```bash
# 1. Database
createuser infraops --pwprompt   # mật khẩu: infraops
createdb -O infraops infraops
createdb -O infraops infraops_test

# 2. Backend
cd backend
uv sync
export INFRAOPS_DATABASE_URL=postgresql+asyncpg://infraops:infraops@localhost:5432/infraops
export INFRAOPS_MASTER_KEYS=$(uv run python -m app.cli generate-master-key)
export INFRAOPS_AUTH_MODE=dev INFRAOPS_BOOTSTRAP_ADMIN_UPNS=admin@congty.local INFRAOPS_SESSION_COOKIE_SECURE=false
export INFRAOPS_STORAGE_DIR=$PWD/var/files   # mẫu biên bản, PDF, bản scan đã ký
uv run alembic upgrade heads
uv run uvicorn app.asgi:app --reload --port 8000

# 3. Worker – chạy tác vụ nền như đồng bộ SDP/BioStar (terminal khác, cùng biến môi trường)
uv run python -m app.worker

# 4. Frontend (terminal khác)
cd frontend
npm install
npm run dev            # http://localhost:5173, /api được proxy tới :8000
```

Chế độ `dev` cho phép đăng nhập bằng UPN không cần mật khẩu (bị chặn ở production).
Đăng nhập `admin@congty.local` để có quyền Quản trị.

> Lưu `INFRAOPS_MASTER_KEYS` lại: đổi key sẽ không giải mã được secret đã lưu.

### Kiểm thử

```bash
cd backend
export INFRAOPS_TEST_DATABASE_URL=postgresql+asyncpg://infraops:infraops@localhost:5432/infraops_test
uv run pytest -q && uv run ruff check . && uv run lint-imports

cd ../frontend
npx tsc -b && TZ=UTC npx vitest run
```

Test không bao giờ gọi hệ thống thật: SDP/BioStar được mock bằng `respx`.

## Triển khai

### docker-compose (1 máy chủ)

```bash
cp .env.example .env     # điền INFRAOPS_MASTER_KEYS, INFRAOPS_SIGNING_SECRET, POSTGRES_PASSWORD
docker compose up -d --build
docker compose run --rm api python -m app.cli create-admin --username admin --name "Quản trị"
```

Mở http://<máy-chủ>:8080 và đăng nhập bằng tài khoản vừa tạo. Compose chạy 4 dịch vụ: `db`, `api`,
`worker` (tác vụ nền: đồng bộ SDP/BioStar) và `web`.

### Kubernetes (Rancher)

1. Build và đẩy image `backend/` → `…/infra-ops/api`, `frontend/` → `…/infra-ops/web`.
2. Tạo Secret `infra-ops-secrets` gồm `INFRAOPS_DATABASE_URL`, `INFRAOPS_MASTER_KEYS`,
   `INFRAOPS_SIGNING_SECRET` (và `INFRAOPS_OIDC_CLIENT_SECRET` khi bật SSO).
3. `helm upgrade --install infra-ops deploy/helm/infra-ops -f my-values.yaml`
   (migration chạy tự động bằng Job trước mỗi lần cài/nâng cấp; chart tạo 3 Deployment: api, worker, web).
4. Tạo quản trị viên đầu tiên:
   `kubectl exec -it deploy/infra-ops-api -- python -m app.cli create-admin --username admin`.
5. File (mẫu, PDF, bản ký) lưu trên PVC `infra-ops-files` (giữ lại khi gỡ chart). Nếu chạy nhiều replica
   api, đặt `storage.accessMode: ReadWriteMany`.
6. Font: mẫu biên bản dùng font **Aptos**. Image có font thay thế (Inter/DejaVu); để PDF giống hệt bản Word,
   tạo Secret chứa file font Aptos (từ bản quyền Office của công ty) và đặt `fontsSecret`.
7. Nếu SDP/BioStar dùng chứng chỉ CA nội bộ: tạo ConfigMap có key `ca.crt` (bundle CA đầy đủ)
   và đặt `internalCaConfigMap` trong values.

## Đăng nhập

| `INFRAOPS_AUTH_MODE` | Dùng khi |
|---|---|
| `local` (mặc định) | Tài khoản nội bộ do Admin tạo; mật khẩu băm scrypt, khoá 15 phút sau 5 lần sai |
| `oidc` | SSO Microsoft Entra ID (khi đã có App Registration): đặt `INFRAOPS_OIDC_TENANT_ID`, `INFRAOPS_OIDC_CLIENT_ID`, `INFRAOPS_OIDC_CLIENT_SECRET`; redirect URI `https://<host>/api/core/auth/oidc/callback` |
| `dev` | Chỉ để phát triển |

Vai trò được gán hoàn toàn trong app (Người dùng & quyền).

## Kết nối hệ thống (Admin)

**Hệ thống → Kết nối hệ thống → Thêm kết nối**, sau đó nhập credential **Read** (đọc, đối soát) và
**Write** (thực thi workflow) riêng, rồi bấm **Kiểm tra kết nối** (kiểm tra DNS → SSL → đăng nhập → quyền thực tế).

### ServiceDesk Plus (on-prem)
1. Tạo technician riêng cho infra-ops (vd. `api.infraops`).
   - Read: quyền xem Requests, Assets, Users.
   - Write: thêm quyền sửa Requests (worklog, đính kèm, trạng thái) và Assets.
2. Đăng nhập SDP bằng technician đó → avatar → **Generate API Key** (đặt ngày hết hạn).
3. Trong infra-ops: Base URL `https://sdp.congty.local:8080`, phương thức **API key / Auth token**,
   nhập ngày hết hạn để Dashboard cảnh báo trước.

### Module Vòng đời nhân sự – đối soát
1. Tạo 2 kết nối trên (ít nhất credential **Read**).
2. **Vòng đời nhân sự → Cấu hình module**: chọn kết nối, kiểm tra tên requester RTR, nhãn trong nội dung
   request, trường Mã NV của user SDP (mặc định `employee_id`; đổi nếu LDAP import vào trường khác), loại tài sản.
3. **Đồng bộ dữ liệu → Đồng bộ ngay** (worker phải đang chạy), sau đó gán các giá trị "Loại biến động" chưa
   phân loại ở Cấu hình module.
4. Xem **Đối soát theo Mã NV**. Worker tự đồng bộ mỗi 30 phút.

### Biên bản Bàn giao / Thu hồi
- **Vòng đời nhân sự → Biên bản → Lập biên bản…**: nhập Mã NV để điền sẵn từ request RTR và thiết bị đang gán
  trên SDP (hoặc mở từ trang Đối soát). Lưu nháp → Xem trước PDF → **Chốt** (cấp số `BG-YYYYMM-####` /
  `TH-YYYYMM-####`, không sửa được nữa) → in, ký → **Tải lên bản đã ký** → **Đính kèm vào request SDP**
  (cần credential Write; đường dẫn upload chỉnh được ở Cấu hình module).
- **Mẫu biên bản**: tải mẫu đang dùng về, sửa trong Word (giữ placeholder `{{ … }}`), tải lên thành phiên bản
  mới, xem thử với dữ liệu mẫu rồi bấm “Dùng phiên bản này”. Danh sách placeholder có ngay trên trang.
- Mẫu gốc được tạo từ file Word của công ty bằng `backend/scripts/build_form_templates.py`.

### Suprema BioStar 2
1. Tạo tài khoản BioStar riêng: Read = quyền xem User/Access Group; Write = Operator/Administrator có quyền quản lý User.
2. Base URL của server BioStar 2 (vd. `https://biostar.congty.local`). Nếu dùng chứng chỉ tự ký,
   tắt **Kiểm tra chứng chỉ SSL** hoặc cài CA nội bộ (khuyến nghị).
