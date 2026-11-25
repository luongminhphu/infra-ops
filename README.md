# infra-ops

Nền tảng web nội bộ cho các tác vụ vận hành của team IT Infrastructure, giao diện tiếng Việt.
Kiến trúc **Platform Core + Modules**: core dùng chung (đăng nhập, phân quyền, kết nối hệ thống, nhật ký kiểm tra,
dashboard, hàng chờ việc, workflow) và các module cắm vào: **Vòng đời nhân sự** (Onboard/Offboard) và
**Công việc Helpdesk** (task SDP, đề xuất mua, PO, nhận hàng).

| Thành phần | Công nghệ |
|---|---|
| Backend | Python 3.13, FastAPI, SQLAlchemy 2 (async), Alembic, PostgreSQL 16, LibreOffice (PDF) |
| Frontend | React 18, Vite, TypeScript, Tailwind CSS 4, TanStack Query/Table |
| Triển khai | Docker / docker compose (1 máy chủ), Helm (Kubernetes / Rancher) |

Tài liệu: [cài đặt Ubuntu + Docker](docs/install-ubuntu-docker.md) · [kiến trúc](docs/architecture.md) ·
[thêm module](docs/adding-a-module.md) · [design system](docs/design-system.md) ·
[đề xuất & quyết định từng phase](docs/phase-0-proposal.md)

## Tính năng

### Nền tảng chung
- **Đăng nhập** bằng tài khoản trong app hoặc SSO Microsoft Entra ID; 5 vai trò (Quản trị, Manager, Supervisor,
  Operator, Chỉ xem) và ma trận phân quyền chỉnh được. Operator chỉ thấy việc của mình và **không thấy giá**.
- **Kết nối hệ thống** ServiceDesk Plus và Suprema BioStar 2: credential Read / Write tách riêng, mã hoá khi lưu,
  *Kiểm tra kết nối* từng bước (DNS → SSL → đăng nhập → quyền), cảnh báo credential sắp hết hạn.
- **Nhật ký kiểm tra**: mọi thay đổi của người dùng và mọi lần ghi ra SDP/BioStar (trước/sau).
- **Việc của tôi**: *Tổng quan* (trễ hạn, đến hạn trong 3 ngày, đang mở, đã xong 7 ngày) và hàng chờ
  **Việc tiếp theo** gom từ mọi module, việc gấp nhất ở trên cùng.
- **Làm từng bước (wizard)** cho Operator: dữ liệu hệ thống đã biết được điền sẵn kèm nguồn, chỉ cần xác nhận;
  Supervisor/Manager giữ màn hình đầy đủ. Nút **?** giải thích cách vận hành ngay cạnh tiêu đề/trường.
- **Worker chạy nền**: đồng bộ định kỳ SDP/BioStar, chạy các bước workflow (đọc trạng thái trước khi ghi, chạy lại an
  toàn, lưu trạng thái trước thay đổi để khôi phục thủ công).
- Giao diện sáng/tối, dùng được trên điện thoại, đạt kiểm tra trợ năng (axe).

### Module Vòng đời nhân sự
- Đồng bộ request **RTR** (nhân viên mới / nghỉ việc), user và tài sản SDP, user BioStar; **đối soát theo Mã NV**
  giữa các hệ thống; xuất Excel.
- **Biên bản Bàn giao / Thu hồi** theo mẫu Word của công ty: mẫu có phiên bản, số tự sinh, xuất PDF, tải bản đã ký,
  đính kèm vào request SDP.
- **Job Onboard / Offboard** tự tạo từ request: gán/thu hồi tài sản SDP, tạo/vô hiệu hoá user BioStar, biên bản,
  ghi chú và đóng request. Chạy ngay hoặc theo ngày hiệu lực; Operator xác nhận từng bước theo checklist kèm bằng
  chứng; người phụ trách theo technician của request, chuyển Job (ghi sang SDP); lưu đồ swimlane theo người.
- **Kho & cấp phát**: band trong RTR × tiêu chuẩn cấp phát × tài sản In Store → tận dụng thiết bị trong kho hoặc
  **lập đề xuất mua** (điền sẵn) cho phần còn thiếu.

### Module Công việc Helpdesk
- Task SDP theo từng Operator (chưa bắt đầu / đang thực hiện / tạm dừng / quá hạn / đã xong), **chuyển việc**
  (đổi technician trên SDP), **thống kê** theo tuần → năm hoặc khoảng ngày tuỳ chọn; tự tạo tài khoản Operator.
- **Đề xuất mua thiết bị** từng bước (gợi ý từ request và từ onboard thiếu máy), bằng chứng Function Head đã duyệt.
- **Định giá**: PC/Laptop theo báo giá từng đề xuất (trên 20 triệu cần 3 báo giá, lấy giá thấp nhất, xác nhận
  *không có hàng* kèm ảnh chụp website); thiết bị khác theo **Bảng giá** cập nhật từ PDF báo giá, **tự nhận diện
  nhà cung cấp** theo mẫu báo giá.
- **PO tổng** theo hạn mức (mặc định 200 triệu, tự mở PO tiếp theo); **Chốt PO** → email nháp `.eml` trình Head of IT
  và Finance (bảng so sánh báo giá, đính kèm Excel, PDF báo giá, bằng chứng).
- **Nhận hàng → nhập kho SDP** từng bước: chọn PO → serial từng máy (quét mã vạch hoặc **đọc từ file biên bản bàn
  giao** PDF/Excel của nhà cung cấp) → **mã tài sản gợi ý** `HCM-<năm>-<loại>-<4 ký tự cuối serial>` → worker tạo
  tài sản *In Store* trên SDP (không tạo trùng serial) → Kho & cấp phát gợi ý ngay cho nhân viên sắp onboard.

Kết nối Microsoft (Entra ID provisioning, AD, Exchange hybrid) đang **tạm hoãn** theo quyết định của chủ sản phẩm.

## Triển khai bằng Docker (khuyến nghị)

Một máy chủ Ubuntu 22.04/24.04, 2 vCPU, 4 GB RAM, 20 GB đĩa, mở được tới SDP/BioStar. `docker compose` chạy 5 dịch vụ:

| Dịch vụ | Vai trò |
|---|---|
| `db` | PostgreSQL 16 (volume `db-data`) |
| `migrate` | Cập nhật cấu trúc database rồi thoát (mỗi lần khởi động) |
| `api` | Backend FastAPI; file mẫu, PDF, bản ký lưu ở volume `files` |
| `worker` | Đồng bộ SDP/BioStar, chạy Job và các lần ghi ra hệ thống ngoài – **bắt buộc chạy** |
| `web` | nginx phục vụ giao diện và chuyển `/api` tới `api`: **HTTP cổng 80, HTTPS cổng 443** (chứng chỉ ở thư mục `certs/`) |

**1. Cài Docker** (Docker Engine + plugin compose, xem [hướng dẫn chi tiết](docs/install-ubuntu-docker.md#3-cài-docker-engine)).

**2. Lấy mã nguồn và tạo `.env`**

```bash
sudo git clone https://github.com/luongminhphu/infra-ops.git /opt/infra-ops
cd /opt/infra-ops
sudo cp .env.example .env && sudo chmod 600 .env
```

Sinh các giá trị bí mật rồi dán vào `.env` (`sudo nano .env`):

```bash
echo "INFRAOPS_MASTER_KEYS=v1:$(openssl rand -base64 32)"
echo "INFRAOPS_SIGNING_SECRET=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
```

| Biến | Giá trị |
|---|---|
| `INFRAOPS_PUBLIC_BASE_URL` | Địa chỉ người dùng mở, có `https`, vd. `https://infraops.congty.local` |
| `INFRAOPS_MASTER_KEYS` / `INFRAOPS_SIGNING_SECRET` / `POSTGRES_PASSWORD` | Các dòng vừa sinh |
| `INFRAOPS_AUTH_MODE` | `local` (tài khoản trong app); `oidc` khi bật SSO Entra |
| `INFRAOPS_ENABLED_MODULES` | `lifecycle,workload` |
| `INFRAOPS_HTTP_BIND` / `INFRAOPS_HTTPS_BIND` | Mặc định `80` / `443`. Chỉ đổi khi máy chủ đã có reverse proxy khác chiếm 80/443 (vd. `127.0.0.1:8080` / `127.0.0.1:8443`) |

> Cất `INFRAOPS_MASTER_KEYS` ở nơi an toàn ngoài máy chủ: mất key là mất mọi credential SDP/BioStar đã nhập.
> Không đưa `.env` vào git.

**3. Chứng chỉ HTTPS.** Cookie phiên có cờ `Secure` nên **bắt buộc truy cập qua HTTPS**. Đặt chứng chỉ của
tên miền (vd. `infraops.congty.local`, cấp bởi CA nội bộ) vào thư mục `certs/` cạnh `docker-compose.yml`:

```bash
sudo mkdir -p /opt/infra-ops/certs
sudo cp fullchain.pem /opt/infra-ops/certs/tls.crt     # chứng chỉ + chuỗi CA trung gian
sudo cp privkey.pem  /opt/infra-ops/certs/tls.key
sudo chown 101 /opt/infra-ops/certs/tls.key && sudo chmod 600 /opt/infra-ops/certs/tls.key   # nginx trong container chạy uid 101
```

Có chứng chỉ: `https://` chạy ở cổng 443, `http://` cổng 80 tự chuyển sang HTTPS. Chưa có chứng chỉ: chỉ chạy HTTP
cổng 80 và trang đăng nhập báo *Cần mở trang bằng HTTPS*. Thử nghiệm khi chưa có chứng chỉ công ty – tạo chứng chỉ tự
ký (trình duyệt sẽ cảnh báo):

```bash
sudo openssl req -x509 -newkey rsa:2048 -nodes -days 365 -subj "/CN=infraops.congty.local" \
  -addext "subjectAltName=DNS:infraops.congty.local,IP:<ip-máy-chủ>" \
  -keyout /opt/infra-ops/certs/tls.key -out /opt/infra-ops/certs/tls.crt
sudo chown 101 /opt/infra-ops/certs/tls.key
```

Thay chứng chỉ (gia hạn): chép đè 2 file rồi `sudo docker compose restart web`.

**4. Build và khởi động**

```bash
sudo docker compose up -d --build
sudo docker compose ps        # db healthy, migrate exited (0), api healthy, worker + web running
sudo docker compose logs web | grep infra-ops   # "HTTPS enabled on 8443…" = đã nhận chứng chỉ
```

Mở cổng 80, 443 trên tường lửa nếu có (`sudo ufw allow 80,443/tcp`).

**5. Tạo quản trị viên đầu tiên** (mật khẩu ≥ 12 ký tự; chạy lại để đặt lại mật khẩu):

```bash
sudo docker compose exec api python -m app.cli create-admin --username admin --name "Quản trị"
```

**6. Mở `https://infraops.congty.local`** (tên miền trỏ về máy chủ trong DNS nội bộ) và đăng nhập `admin`. Máy chủ đã
có nginx/reverse proxy khác giữ cổng 80/443: đặt `INFRAOPS_HTTP_BIND=127.0.0.1:8080`, `INFRAOPS_HTTPS_BIND=127.0.0.1:8443`
và để proxy đó lo HTTPS ([mục 8 của hướng dẫn](docs/install-ubuntu-docker.md#8-https)).

**7. Cấu hình trong ứng dụng**: *Hệ thống → Kết nối hệ thống* thêm SDP, BioStar và bấm
*Kiểm tra kết nối* → *Người dùng & quyền* tạo tài khoản, gán vai trò → *Cấu hình module* của Vòng đời nhân sự và
Công việc Helpdesk → *Đồng bộ ngay*. Chi tiết từng bước ở các mục dưới.

**Vận hành**

```bash
sudo docker compose logs -f --tail 100 api worker                     # xem log
cd /opt/infra-ops && sudo git pull && sudo docker compose up -d --build   # nâng cấp (migrate tự chạy)
sudo docker compose exec -T db pg_dump -U infraops -Fc infraops > db-$(date +%F).dump   # sao lưu database
```

Sao lưu cả volume `files` (mẫu, PDF, bản ký), khôi phục, CA nội bộ cho SDP/BioStar, font Aptos cho PDF, máy chủ không
ra Internet và xử lý sự cố: xem **[docs/install-ubuntu-docker.md](docs/install-ubuntu-docker.md)**.

## Triển khai trên Kubernetes (Rancher)

1. Build và đẩy image `backend/` → `…/infra-ops/api`, `frontend/` → `…/infra-ops/web`.
2. Tạo Secret `infra-ops-secrets` gồm `INFRAOPS_DATABASE_URL`, `INFRAOPS_MASTER_KEYS`,
   `INFRAOPS_SIGNING_SECRET` (và `INFRAOPS_OIDC_CLIENT_SECRET` khi bật SSO).
3. `helm upgrade --install infra-ops deploy/helm/infra-ops -f my-values.yaml`
   (migration chạy tự động bằng Job trước mỗi lần cài/nâng cấp; chart tạo 3 Deployment: api, worker, web).
4. Tạo quản trị viên đầu tiên:
   `kubectl exec -it deploy/infra-ops-api -- python -m app.cli create-admin --username admin`.
5. File (mẫu, PDF, bản ký) lưu trên PVC `infra-ops-files` (giữ lại khi gỡ chart), dùng chung cho api và
   worker. Với `ReadWriteOnce`, api và worker phải chạy trên cùng một node; nếu không (hoặc chạy nhiều
   replica api), đặt `storage.accessMode: ReadWriteMany`.
6. Font: mẫu biên bản dùng font **Aptos**. Image có font thay thế (Inter/DejaVu); để PDF giống hệt bản Word,
   tạo Secret chứa file font Aptos (từ bản quyền Office của công ty) và đặt `fontsSecret`.
7. Nếu SDP/BioStar dùng chứng chỉ CA nội bộ: tạo ConfigMap có key `ca.crt` (bundle CA đầy đủ)
   và đặt `internalCaConfigMap` trong values.

## Phát triển (chạy không dùng Docker)

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

## Lộ trình theo phase

| Phase | Nội dung | Trạng thái |
|---|---|---|
| 0 | Hiện trạng, kiến trúc, câu hỏi mở | Xong |
| 1 | Platform Core: module, đăng nhập, RBAC, kết nối hệ thống (SDP, BioStar), audit, design system | Xong |
| 2 | SDP + BioStar: đọc request RTR, user, tài sản, user BioStar; đối soát theo Mã NV; export; bộ lập lịch nền | Xong |
| 3 | Biên bản Bàn giao / Thu hồi theo mẫu thật: mẫu có phiên bản, số tự sinh, PDF, tải bản ký, đính kèm SDP | Xong |
| 4 | Workflow Onboard / Offboard từ request SDP (gán/thu hồi tài sản, BioStar, biên bản, đóng request) | Xong |
| 5 | Vai trò Manager / Supervisor / Operator / Viewer; module **Công việc Helpdesk**: task SDP theo Operator, chuyển việc (ghi sang SDP), thống kê theo kỳ | Xong |
| 6 | Lưu đồ Onboard/Offboard, checklist từng bước, Operator xác nhận kèm bằng chứng | Xong |
| 7 | Giao diện “Phòng điều khiển”: rail phân hệ + menu con, màu cobalt, font Inter, cho toàn app | Xong |
| 8 | **Việc của tôi** cho Operator (task SDP theo trạng thái, Job onboard/offboard, việc cần chuẩn bị), tự tạo tài khoản Operator, Job theo technician request RTR + chuyển Job (ghi sang SDP), lưu đồ swimlane theo người, đề xuất mua thiết bị | Xong |
| 9 | **PO tổng** theo hạn mức (mặc định 200 triệu, tự mở PO tiếp theo), bảng giá do Supervisor cập nhật, bằng chứng Function Head, **Chốt PO** → email nháp .eml; **Kho & cấp phát**: band trong RTR × tiêu chuẩn cấp phát × tài sản In Store → tận dụng hoặc đề xuất mua; tooltip hướng dẫn | Xong |
| 10 | **Nhận hàng → nhập kho SDP**: nhập serial/asset tag, worker tạo tài sản In Store trên SDP (không tạo trùng serial), Kho & cấp phát đồng bộ lại để gợi ý cho onboard | Xong |
| 11 | Operator chỉ đề xuất thiết bị (không thấy giá); PC/Laptop định giá theo **báo giá từng đề xuất** (trên 20 triệu cần 3 báo giá, lấy giá thấp nhất); thiết bị khác **cập nhật giá từ file báo giá PDF** | Xong |
| 12 | **Nhận diện báo giá theo nhà cung cấp** (từ khoá + cột đơn giá của từng mẫu), xác nhận **không có hàng** kèm ảnh chụp website, **email trình phê duyệt PO** cho Head of IT và Finance (bảng so sánh báo giá) | Xong |
| 13 | **Làm từng bước (wizard)**: khung Wizard dùng chung; Job Onboard/Offboard cho Operator mở thẳng dạng từng bước, dữ liệu biết trước được điền sẵn để chỉ cần xác nhận | Xong |
| 14 | **Đề xuất mua từng bước**: Thiết bị → Người dùng & lý do → Bằng chứng Function Head → Xem lại & gửi, điền sẵn từ request/onboard | Xong |
| 15 | **Việc tiếp theo**: Việc của tôi thành một hàng chờ theo ưu tiên từ mọi module, nút *Làm việc tiếp theo* | Xong |
| 16 | **Nhận hàng từng bước**: chọn PO → quét serial từng máy → xác nhận ghi SDP → kết quả + Job onboard còn thiếu máy | Xong |
| 17 | **Tổng quan** đầu Việc của tôi (trễ hạn / đến hạn 3 ngày / đang mở / đã xong 7 ngày); Operator nhận hàng, **đọc serial từ file biên bản** (PDF/Excel), **gợi ý mã tài sản** HCM-năm-loại-4 số cuối serial; sửa theo critique luồng Operator | Xong |
| 18 | Triển khai: trang đăng nhập báo rõ khi mở bằng `http://` (cookie phiên `Secure` bị trình duyệt bỏ) thay vì đăng nhập thất bại không lý do; container `web` phục vụ thẳng **HTTP 80 / HTTPS 443** với chứng chỉ trong `certs/` | Xong |
| sau | Kết nối Microsoft (Entra ID, AD, Exchange hybrid) | Tạm hoãn |

## Đăng nhập

| `INFRAOPS_AUTH_MODE` | Dùng khi |
|---|---|
| `local` (mặc định) | Tài khoản nội bộ do Admin tạo; mật khẩu băm scrypt, khoá 15 phút sau 5 lần sai |
| `oidc` | SSO Microsoft Entra ID (khi đã có App Registration): đặt `INFRAOPS_OIDC_TENANT_ID`, `INFRAOPS_OIDC_CLIENT_ID`, `INFRAOPS_OIDC_CLIENT_SECRET`; redirect URI `https://<host>/api/core/auth/oidc/callback`. Thêm `INFRAOPS_ALLOW_LOCAL_LOGIN=true` để vẫn cho đăng nhập bằng tài khoản nội bộ (mục phụ dưới nút Microsoft) |
| `dev` | Chỉ để phát triển |

Vai trò được gán hoàn toàn trong app (Người dùng & quyền), xếp từ ít quyền đến nhiều quyền nhất:

| Vai trò | Quyền chính |
|---|---|
| Viewer | Chỉ đọc (thấy task của mọi Operator) |
| Operator | Thực hiện các task trên app (Job onboard/offboard, biên bản, đồng bộ, đề xuất mua, nhận hàng); **chỉ thấy task và thống kê của mình, không thấy giá** |
| Supervisor | Quyền của Operator + xem task của mọi Operator và điều phối/chuyển task (ghi sang SDP). Không cấu hình hệ thống/module, không quản lý người dùng |
| Manager | Quyền của Supervisor + cấu hình module (Vòng đời, Công việc, mẫu biên bản), buộc đóng Job, xem nhật ký |
| Administrator | Toàn quyền, cấu hình kết nối hệ thống và phân quyền |

Kết nối hệ thống và người dùng/phân quyền chỉ Administrator thay đổi. Administrator có thể chỉnh quyền từng vai trò trong ma trận phân quyền.

**Khớp tài khoản SSO Entra ID** (`INFRAOPS_AUTH_MODE=oidc`): *Tên đăng nhập* trong app phải là **UPN** của người dùng
trên Entra ID (địa chỉ đăng nhập Microsoft, vd. `nguyen.van.a@congty.vn`; lấy từ claim `preferred_username`, không
phân biệt hoa thường) – không phải tên ngắn hay e-mail phụ. Lần đăng nhập SSO đầu tiên, app tìm tài khoản theo UPN đó
và ghi lại Object ID (`oid`) của Entra; từ đó luôn khớp theo `oid`, nên đổi UPN trên Entra sau này vẫn giữ đúng tài
khoản. Không tìm thấy thì app tự tạo tài khoản vai trò **Viewer**. Vì vậy, để gán quyền trước khi người dùng đăng nhập,
tạo tài khoản với Tên đăng nhập = UPN (ở chế độ SSO form không có ô mật khẩu). Tài khoản `admin` tạo bằng lệnh
`create-admin` là tài khoản nội bộ, chỉ đăng nhập được ở chế độ `local`; khi chuyển sang SSO, đặt
`INFRAOPS_BOOTSTRAP_ADMIN_UPNS=<UPN của quản trị viên>` để người đó nhận vai trò Administrator ở lần đăng nhập SSO.

### Cấu hình SSO Microsoft Entra ID

infra-ops dùng OpenID Connect (authorization code + PKCE, client secret) với endpoint v2.0 của tenant công ty.
Cần: HTTPS đã chạy đúng tên miền (bước 3 phần Docker), máy chủ ra được `login.microsoftonline.com:443`, và người cấu
hình có quyền **Application Administrator** (hoặc Cloud Application Administrator) trên Entra ID.

**1. Tạo App registration** – [Entra admin center](https://entra.microsoft.com) → *Identity → Applications → App
registrations → New registration*:
- *Name*: `infra-ops`.
- *Supported account types*: **Accounts in this organizational directory only (Single tenant)**.
- *Redirect URI*: nền tảng **Web**, giá trị `<INFRAOPS_PUBLIC_BASE_URL>/api/core/auth/oidc/callback`, vd.
  `https://infraops.congty.local/api/core/auth/oidc/callback` – phải **trùng từng ký tự** với `INFRAOPS_PUBLIC_BASE_URL`
  trong `.env` (https, không có `/` thừa, không có cổng nếu dùng 443).
- Bấm *Register*. Ở trang *Overview*, ghi lại **Application (client) ID** và **Directory (tenant) ID**.

**2. Tạo client secret** – *Certificates & secrets → Client secrets → New client secret*: mô tả `infra-ops`, thời hạn
12–24 tháng. Chép ngay cột **Value** (không phải *Secret ID*; Value chỉ hiện một lần) và ghi lịch nhắc gia hạn trước
ngày hết hạn.

**3. Quyền API** – *API permissions*: giữ **Microsoft Graph → User.Read** (delegated) và thêm delegated **openid**,
**profile**, **email** (*Add a permission → Microsoft Graph → Delegated permissions → OpenId permissions*). Bấm
**Grant admin consent for <tên công ty>** để người dùng không phải tự đồng ý khi đăng nhập lần đầu.

**4. Giới hạn ai được đăng nhập (khuyến nghị)** – *Enterprise applications → infra-ops → Properties*: đặt
**Assignment required? = Yes**, rồi *Users and groups → Add user/group* chọn nhóm IT (vd. `IT-Infra`). Người ngoài
nhóm bị Entra từ chối (lỗi `AADSTS50105`) trước khi vào app. Có thể bật thêm Conditional Access / MFA cho ứng dụng này.

**5. Tạo sẵn tài khoản trong app (tuỳ chọn)** – còn đang ở chế độ `local`, đăng nhập `admin` → *Hệ thống → Người dùng &
quyền → Thêm người dùng*: **Tên đăng nhập = UPN** trên Entra (vd. `nguyen.van.a@congty.vn`), chọn vai trò. Ai chưa
được tạo sẵn sẽ vào với vai trò Viewer ở lần đăng nhập đầu, Administrator gán lại sau.

**6. Bật SSO trong `.env`** rồi áp dụng:

```bash
INFRAOPS_AUTH_MODE=oidc
INFRAOPS_PUBLIC_BASE_URL=https://infraops.congty.local
INFRAOPS_OIDC_TENANT_ID=<Directory (tenant) ID>
INFRAOPS_OIDC_CLIENT_ID=<Application (client) ID>
INFRAOPS_OIDC_CLIENT_SECRET=<Value của client secret>
INFRAOPS_BOOTSTRAP_ADMIN_UPNS=admin.it@congty.vn   # UPN nhận vai trò Administrator khi đăng nhập SSO (nhiều người: cách nhau dấu phẩy)
INFRAOPS_ALLOW_LOCAL_LOGIN=true   # tuỳ chọn: dưới nút Microsoft có thêm "Đăng nhập bằng tài khoản nội bộ"
```

```bash
sudo docker compose up -d        # tạo lại api/worker với cấu hình mới
```

**7. Kiểm tra** – mở `https://infraops.congty.local` → **Đăng nhập bằng tài khoản Microsoft** → đăng nhập bằng tài
khoản trong `INFRAOPS_BOOTSTRAP_ADMIN_UPNS` → menu tài khoản ở góc phải hiện *Vai trò: Administrator*. Sau đó gán vai trò cho các
thành viên khác ở *Người dùng & quyền*.

Lưu ý:
- Ở chế độ `oidc`, trang đăng nhập mặc định chỉ có nút **Đăng nhập bằng tài khoản Microsoft**. Đặt
  `INFRAOPS_ALLOW_LOCAL_LOGIN=true` để bên dưới có thêm mục **Đăng nhập bằng tài khoản nội bộ** (thu gọn sẵn): chỉ tài
  khoản có mật khẩu trong infra-ops đăng nhập được (vd. `admin` tạo bằng `create-admin`, hoặc tài khoản Administrator đặt
  mật khẩu trong *Người dùng & quyền* – ô mật khẩu là tuỳ chọn); người chỉ dùng SSO vẫn phải bấm nút Microsoft. Nên
  giữ ít nhất một tài khoản nội bộ Administrator làm **tài khoản dự phòng** khi Entra/Internet gặp sự cố. Tắt hẳn đăng
  nhập nội bộ: để `false`. Quay về chỉ dùng tài khoản nội bộ: `INFRAOPS_AUTH_MODE=local`, rồi `sudo docker compose up -d`.
- Gia hạn client secret: tạo secret mới trên Entra → thay `INFRAOPS_OIDC_CLIENT_SECRET` → `sudo docker compose up -d`
  → xoá secret cũ trên Entra.
- Vô hiệu hoá một người trong app (*Người dùng & quyền*) chặn đăng nhập ngay cả khi tài khoản Entra còn hoạt động;
  khoá tài khoản trên Entra chặn ở lần đăng nhập tiếp theo (phiên hiện tại hết hạn sau tối đa 10 giờ).

| Lỗi khi đăng nhập | Nguyên nhân / cách sửa |
|---|---|
| `AADSTS50011` (redirect URI mismatch) | Redirect URI trên App registration khác `INFRAOPS_PUBLIC_BASE_URL` + `/api/core/auth/oidc/callback` – sửa cho trùng |
| `AADSTS7000215` (invalid client secret) | Dùng nhầm *Secret ID* thay cho *Value*, hoặc secret đã hết hạn – tạo secret mới |
| `AADSTS50105` (user not assigned) | Đã bật *Assignment required* nhưng người dùng chưa thuộc nhóm được gán – thêm vào *Users and groups* |
| `AADSTS65001` (consent required) | Chưa *Grant admin consent* ở bước 3 |
| Quay lại trang đăng nhập với thông báo “Đăng nhập Microsoft không thành công” | Xem `sudo docker compose logs api`: thường do máy chủ không ra được `login.microsoftonline.com`, sai tenant ID, hoặc giờ máy chủ lệch (kiểm tra NTP) |
| Đăng nhập được nhưng chỉ là Viewer | UPN chưa được tạo sẵn / chưa nằm trong `INFRAOPS_BOOTSTRAP_ADMIN_UPNS` (so khớp theo UPN, không theo e-mail phụ) – Administrator gán vai trò trong app |

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
4. **Key cá nhân của từng người (bắt buộc cho thao tác ghi):** credential Read chung chỉ dùng cho đồng bộ nền. Job
   onboard/offboard (đọc + ghi, theo người phụ trách Job), nhận hàng, chuyển việc và đính kèm biên bản chạy bằng
   **API key SDP của chính người thực hiện**, nên SDP ghi nhận đúng ai làm. Mỗi người vào menu tài khoản →
   **Khoá API của tôi**, dán API key technician của mình (SDP: ảnh đại diện → Hồ sơ cá nhân → tạo API key), bấm
   **Kiểm tra**. Key mã hoá khi lưu, không ai xem lại được (kể cả quản trị). Chưa nhập key thì bước Job dừng ở trạng
   thái chờ và tự chạy lại sau khi lưu; "Việc của tôi" nhắc "Nhập API key cá nhân".

### Module Vòng đời nhân sự – đối soát
1. Tạo 2 kết nối trên (ít nhất credential **Read**).
2. **Vòng đời nhân sự → Cấu hình module**: chọn kết nối, kiểm tra tên requester RTR, nhãn trong nội dung
   request, trường Mã NV của user SDP (mặc định `employee_id`; đổi nếu LDAP import vào trường khác), loại tài sản.
3. **Đồng bộ dữ liệu → Đồng bộ ngay** (worker phải đang chạy), sau đó gán các giá trị "Loại biến động" chưa
   phân loại ở Cấu hình module.
4. Xem **Đối soát theo Mã NV**. Worker tự đồng bộ mỗi 30 phút.
5. **Nhiều hệ thống BioStar** (vd. *BioStar 2 - Office* là hệ thống chính, *BioStar 2 - Server* là nguồn phụ): ở **Cấu hình
   module → Kết nối sử dụng**, chọn hệ thống chính ở ô *BioStar* (Job tạo/khoá user ở đây), rồi **Thêm hệ thống
   BioStar** cho nguồn phụ. **SDP là chuẩn**: Mã NV trên mọi BioStar và SDP được so theo **5 số cuối** (vd. user
   `100008` → Mã NV `000008`); Mã NV và họ tên hiển thị lấy theo user SDP. Nếu hai Mã NV trên SDP trùng 5 số cuối
   thì không tự ghép (tránh nhầm người); mã không khớp SDP vẫn hiện là lệch. Mỗi lần đồng bộ đọc cả hai và gộp theo Mã NV; người có user ở cả hai
   nơi thì user đang hoạt động được ưu tiên (để phát hiện người đã nghỉ còn quyền ra vào). Một hệ thống đọc lỗi thì cả
   cột BioStar được bỏ qua lần đó, không kết luận sai.
6. **Nhóm nghỉ việc trên BioStar**: nếu quy trình là chuyển user nghỉ việc vào một nhóm (vd. `0.NGHIVIEC`) thay vì xoá,
   nhập tên nhóm ở **Cấu hình module → Kết nối sử dụng**. User trong nhóm đó coi như đã nghỉ việc: không đối soát với
   SDP và bị ẩn khỏi trang Đối soát (dòng đầu trang ghi số user đã ẩn), trừ khi còn request RTR liên quan; khi đó ô
   BioStar ghi *Nghỉ việc* và không báo “còn hoạt động” khi offboard.

### Biên bản Bàn giao / Thu hồi
- **Vòng đời nhân sự → Biên bản → Lập biên bản…**: nhập Mã NV để điền sẵn từ request RTR và thiết bị đang gán
  trên SDP (hoặc mở từ trang Đối soát). Lưu nháp → Xem trước PDF → **Chốt** (cấp số `BG-YYYYMM-####` /
  `TH-YYYYMM-####`, không sửa được nữa) → in, ký → **Tải lên bản đã ký** → **Đính kèm vào request SDP**
  (cần credential Write; đường dẫn upload chỉnh được ở Cấu hình module).
- **Mẫu biên bản**: tải mẫu đang dùng về, sửa trong Word (giữ placeholder `{{ … }}`), tải lên thành phiên bản
  mới, xem thử với dữ liệu mẫu rồi bấm “Dùng phiên bản này”. Danh sách placeholder có ngay trên trang.
- Mẫu gốc được tạo từ file Word của công ty bằng `backend/scripts/build_form_templates.py`.

### Job Onboard / Offboard
1. Nhập thêm credential **Write** cho ServiceDesk Plus (technician có quyền sửa Request, Asset) và BioStar
   (tài khoản quản lý User). Nên thử trước trên kết nối **Test**.
2. **Cấu hình module → Job onboard / offboard**: trạng thái đóng request (mặc định `Resolved`), bấm
   **Tải danh sách nhóm từ BioStar** để chọn nhóm người dùng và nhóm quyền ra vào mặc định, chọn vô hiệu hoá
   hay xoá user BioStar khi offboard.
3. Sau mỗi lần đồng bộ, mỗi request *Nhân viên mới* / *Nghỉ việc* có một **Job nháp** (không ghi gì ra ngoài).
4. Mở Job → (onboard) chọn tài sản trong kho → **Xem trước các bước** → **Bắt đầu ngay** hoặc **Lên lịch**
   theo ngày hiệu lực. Worker chạy các bước tự động; Helpdesk xác nhận các bước thủ công (đăng ký vân tay/thẻ,
   tình trạng tài sản thu hồi), nhập lại Mã NV trước khi vô hiệu hoá BioStar, chốt và tải biên bản đã ký.
   Job tự đính kèm biên bản, ghi chú và đóng request SDP.
5. **Kiểm tra từng bước:** mỗi bước làm xong chuyển sang *Chờ kiểm tra*. Operator kiểm tra trên hệ thống thật,
   tick đủ checklist, đính kèm bằng chứng nếu bước được đánh dấu (ảnh chụp màn hình – dán Ctrl+V được, PDF/DOCX,
   liên kết, ghi chú) rồi **Xác nhận hoàn tất**; bước sau chỉ chạy khi bước trước đã xác nhận. **Báo không đạt**
   (kèm lý do) chuyển bước sang lỗi để sửa và chạy lại. Nút **Lưu đồ** trên Job hiện toàn bộ quy trình và tiến độ.
6. **Lưu đồ quy trình** (menu Vòng đời nhân sự): sơ đồ Onboard / Offboard và checklist từng bước. Manager bấm
   **Sửa checklist** để thêm/sửa/xoá mục và chọn bước bắt buộc bằng chứng; mỗi lần lưu là một phiên bản mới
   (có lịch sử, ghi nhật ký). Job đang chạy giữ phiên bản lúc bắt đầu.
7. Bước lỗi có thể **Chạy lại** (an toàn: bước đọc trạng thái hiện tại trước khi ghi); trạng thái trước khi thay
   đổi được lưu ở từng bước để khôi phục thủ công. Manager có thể **Buộc đóng Job** kèm lý do (mục *Kết thúc Job sớm* cuối trang Job).

### Module Công việc Helpdesk
1. **Cấu hình công việc**: chọn kết nối SDP (dùng credential Read để đọc, Write để chuyển việc), nhóm hỗ trợ cần
   lấy (để trống = tất cả) và các trạng thái được tính là *đã thực hiện* / *đã huỷ*.
2. Worker đọc technician và request mỗi 10 phút (lần đầu đọc 400 ngày gần nhất, sau đó chỉ đọc request mới cập nhật).
   Technician tự liên kết với tài khoản infra-ops khi e-mail trên SDP trùng tên đăng nhập; chọn tay ở bảng
   **Operator ↔ Technician SDP** nếu khác.
3. **Công việc**: task đang chờ / quá hạn / đã thực hiện theo từng Operator (Operator chỉ thấy task của mình).
   Manager / Supervisor chọn task →
   **Chuyển việc** (vd. khi nghỉ phép): infra-ops đổi Technician trên SDP qua worker, ghi nhật ký; lỗi hiện ngay
   trên trang kèm nút **Thử lại**.
4. **Thống kê công việc**: tuần / tháng / quý / 6 tháng / 1 năm hoặc khoảng ngày tuỳ chọn; tiếp nhận, hoàn tất,
   đúng hạn / trễ hạn, thời gian xử lý trung bình, đang chờ và quá hạn theo Operator.

5. **Trạng thái task:** request đang mở được chia *Chưa bắt đầu* / *Đang thực hiện* / *Tạm dừng* theo trạng thái SDP
   (cấu hình ở **Cấu hình công việc**, mặc định `In Progress`, `On Hold`/`Pending`). Bật **Tự tạo tài khoản
   Operator** để mỗi technician có e-mail mà chưa có tài khoản được tạo tài khoản vai trò Operator (đăng nhập SSO
   bằng e-mail đó) và liên kết sẵn.
6. **Đề xuất mua thiết bị** (chỉ lưu trong infra-ops, không ghi lên SDP):
   - Gợi ý từ request có từ khoá thay thế / nâng cấp / sửa chữa (ở **Việc của tôi**) và từ **Kho & cấp phát**
     (onboard thiếu thiết bị). Operator **Lập đề xuất**: chọn thiết bị trong bảng giá, số lượng, người dùng, lý do và
     đính kèm **bằng chứng Function Head đã duyệt** (PNG/JPG/PDF, dán ảnh Ctrl+V).
   - Operator chỉ chọn thiết bị cần cấp – **không thấy giá**, PO tổng hay Bảng giá (quyền `workload.price.view`
     chỉ có ở Supervisor/Manager; file Excel xuất ra của Operator cũng không có cột giá).
   - **PC/Laptop** (nhóm cấu hình được) định giá theo **báo giá cho từng đề xuất**: khi duyệt, Supervisor thêm báo
     giá của đối tác (tên, đơn giá, file PDF); hệ thống lấy **giá thấp nhất**. Giá thấp nhất trên 20.000.000 đ
     (cấu hình được) thì cần đủ **3 báo giá** mới duyệt được. File báo giá được đính kèm email khi chốt PO.
   - **Thiết bị khác**: Bảng giá → **Cập nhật giá từ PDF**: tải file báo giá của đối tác, hệ thống đọc chữ trong PDF,
     tìm thiết bị có trong Bảng giá và gợi ý đơn giá (số tiền nhỏ nhất trên dòng, tức đơn giá); Supervisor kiểm tra,
     sửa, bỏ chọn rồi **Cập nhật**. File PDF lưu kèm từng thiết bị. PDF bản scan (ảnh) không đọc được – nhập tay.
   - **Nhà cung cấp** (thẻ cùng tên): khai báo 4–5 nhà cung cấp thường xuyên với **từ khoá nhận diện** (mã số thuế,
     tên miền…) và **cột đơn giá** trên mẫu báo giá của họ (vd. mẫu “Giá niêm yết | Đơn giá | Thành tiền” → số tiền thứ
     2). Tải PDF báo giá (Bảng giá hoặc báo giá PC/Laptop của đề xuất) → hệ thống tự nhận ra nhà cung cấp và đọc đơn giá
     theo mẫu đó; Supervisor kiểm tra rồi thêm.
   - **Không có hàng**: khi không đủ 3 báo giá, Supervisor chọn *Nhà cung cấp không có hàng*, đính kèm ảnh chụp
     website (dán Ctrl+V được). Dòng này được tính vào số báo giá bắt buộc; vẫn cần ít nhất 1 báo giá có giá.
   - **Duyệt** (Supervisor/Manager) cần bằng chứng và thiết bị có giá; đề xuất đã duyệt tự vào **PO tổng** đang mở
     theo thứ tự duyệt, PO không đủ chỗ dưới **hạn mức** (Cấu hình công việc, mặc định 200.000.000 đ) thì hệ thống
     tự mở PO tiếp theo. Đổi giá/hạn mức thì các PO nháp được gom lại.
   - **Chốt PO**: cấp số `PO-<năm>-<STT>`, khoá đơn giá, chuyển đề xuất sang *Đã đặt mua* và tải file **.eml** – mở
     bằng Outlook là **email nháp trình phê duyệt PO** gửi Head of IT và Phòng Finance (người nhận + mẫu ở Cấu hình
     công việc): bảng từng hạng mục với các báo giá so sánh, giá được chọn, nhà cung cấp không có hàng; đính kèm Excel
     chi tiết, PDF báo giá, ảnh chụp không có hàng và bằng chứng Function Head. Kiểm tra rồi gửi. Hệ thống đã lưu mẫu
     email cũ thì bấm Hoàn tác/sửa nội dung để dùng biến mới `{{ items_table }}` (mẫu cũ vẫn có bảng trong bản HTML).
   - **Nhận hàng** (Operator, Supervisor, Manager – từ *Việc của tôi* hoặc nút *Nhận hàng* ở PO tổng), từng bước:
     chọn PO đã chốt → nhập serial từng máy (quét mã vạch, Enter sang ô kế tiếp) hoặc **Đọc serial từ file biên bản**
     của nhà cung cấp: Excel có cột tiêu đề *Serial* / *S/N* / *Số serial* (cột *Asset tag* / *Mã tài sản* nếu có) hoặc
     PDF có chữ (nhãn `S/N:` hoặc bảng có cột Serial; PDF scan không đọc được) – serial điền vào ô trống, khớp theo tên
     thiết bị, file không được lưu. **Mã tài sản** được gợi ý `HCM-<năm>-<loại>-<4 ký tự cuối serial>` (tiền tố và mã
     loại theo nhóm bảng giá chỉnh ở Cấu hình công việc; mặc định LT = Laptop, PC = PC/Desktop, MN = Màn hình, nhóm khác
     lấy chữ cái đầu), sửa tay được; serial/mã trùng bị chặn, dữ liệu đang nhập giữ lại khi tải lại trang. Xác nhận →
     worker tạo tài sản *In Store* trên SDP bằng credential Write (serial đã có trên SDP thì giữ nguyên, lỗi giữa chừng
     **Thử lại** an toàn), đề xuất thành *Đã nhận hàng*, **Kho & cấp phát** đồng bộ lại để gợi ý cho nhân viên sắp
     onboard. Linh kiện/sửa chữa (không có *Sản phẩm trên SDP* trong bảng giá) chỉ đánh dấu đã nhận.
   - **Xuất Excel** toàn bộ đề xuất (đơn giá, thành tiền, PO).

### Việc của tôi (Operator)
Trang **Việc của tôi** (rail trái / thanh tab trên điện thoại) gom mọi việc của người đang đăng nhập. Đầu trang là
**Tổng quan** – số task SDP và Job *trễ hạn*, *đến hạn trong 3 ngày*, *đang mở*, *đã xong 7 ngày qua* (bấm để mở danh
sách) – để sắp xếp kế hoạch trong ngày. Bên dưới là hàng chờ *Việc tiếp theo* (mục sau); “Xem theo nhóm” vẫn có:
- **Onboard / Offboard:** Job có request RTR giao cho bạn trên SDP, chia *Cần bạn xử lý ngay* (bước chờ xác nhận,
  lỗi), *Đang chạy*, *Sắp tới – cần chuẩn bị* (kèm danh sách việc chuẩn bị: chọn tài sản, kiểm tra user SDP…) và
  *Đã xong gần đây*.
- **Task Helpdesk:** đang thực hiện / chưa bắt đầu / tạm dừng / quá hạn / xong 7 ngày qua, và gợi ý đề xuất mua thiết bị.

Người phụ trách Job lấy theo **technician của request RTR** trên SDP (đồng bộ lại mỗi lần sync). Manager /
Supervisor bấm **Chuyển Job** trên trang Job: worker đổi technician của request trên SDP rồi ghi lịch sử phụ trách.
**Lưu đồ** của Job vẽ mỗi người phụ trách một làn; mũi tên nhảy làn đánh dấu lúc chuyển giao (giờ, trên SDP hay
trong infra-ops).

### Việc tiếp theo
**Việc của tôi** là một hàng chờ duy nhất, việc gấp nhất ở trên cùng – bấm **Làm việc tiếp theo**, làm xong quay lại
để sang việc kế. Thứ tự: *Cần bạn làm ngay* (bước Job chờ bạn xác nhận/thực hiện, bước lỗi) → *Quá hạn / gấp*
(task SDP quá hạn gộp thành một mục, onboard/offboard hiệu lực trong 3 ngày cần chuẩn bị, đề xuất chờ duyệt với
Supervisor) →
*Sắp đến hạn* (task đến hạn trong ngày, Job cần chuẩn bị, đề xuất thiếu bằng chứng) → *Cần biết* (Job đứng yên,
hàng đã đặt chờ nhận) →
*Gợi ý* (request có thể cần mua thiết bị). Các nhóm cũ vẫn xem được ở “Xem theo nhóm”.

### Làm từng bước (Operator)
Operator mở một Job Onboard/Offboard là vào **chế độ từng bước** (Supervisor/Manager mặc định thấy màn chi tiết
đầy đủ; ai cũng chuyển được bằng *Làm từng bước* / *Xem chi tiết đầy đủ*):
1. **Thông tin nhân viên** – lấy từ request RTR, chỉ cần kiểm tra (sai thì sửa trên SDP rồi đồng bộ).
2. **User SDP** – hệ thống tìm theo Mã NV, bấm *Đúng – dùng user này* (chưa có thì bước chạy sẽ tự chờ AD đồng bộ).
3. **Thiết bị cấp phát** (onboard) – tài sản In Store tận dụng được theo band đã chọn sẵn; mỗi thiết bị thiếu có nút
   **Lập đề xuất mua** (điền sẵn, gửi xong quay lại Job; hiện *Đã đề xuất* khi đã lập).
4. **Bắt đầu** – xem trước việc hệ thống sẽ làm; ngày hiệu lực trong tương lai thì nút chính là *Chạy vào ngày
   hiệu lực*.
Khi Job chạy, màn hình chỉ hiện **bước hiện tại** với một dòng *Bạn cần: …* (xác nhận checklist, bằng chứng, nhập
Mã NV…) hoặc *Không cần làm gì* khi hệ thống đang tự chạy.

**Đề xuất mua thiết bị** cũng làm từng bước (*Lập đề xuất* ở Việc của tôi, *Lập đề xuất mua* ở Kho & cấp phát, hoặc
*Đề xuất mới*): **Thiết bị** (mô tả, loại, số lượng điền sẵn từ request/onboard) → **Người dùng & lý do** →
**Bằng chứng Function Head** (chọn file hoặc dán ảnh; *Chưa có – bổ sung sau* nếu cần) → **Xem lại & gửi**.

### Kho & cấp phát (Vòng đời nhân sự)
1. **Cấu hình module → Tiêu chuẩn cấp phát thiết bị theo band**: mỗi dòng là một thiết bị (band áp dụng, loại tài
   sản SDP, model chứa – tuỳ chọn, số lượng, tên trong bảng giá). Band lấy từ dòng `Band:` trong request RTR (đổi
   nhãn ở mục Request RTR); thiếu band thì Operator nhập tay trên trang Kho & cấp phát.
2. **Kho & cấp phát** dùng tài sản In Store, chưa gán user, của lần đồng bộ gần nhất: nhân viên có ngày hiệu lực
   sớm hơn được ưu tiên; tài sản đã chọn trên Job nháp giữ cho Job đó. Mỗi dòng hiện *Tận dụng* (tài sản cụ thể) hoặc
   *Cần mua* → **Lập đề xuất mua** (điền sẵn thiết bị, số lượng, người dùng). Bấm **Xem chi tiết** trên ô tồn kho của từng loại thiết bị để
   xem danh sách tài sản In Store (tên, asset tag, serial, sản phẩm, số lượng theo model) và tài sản nào *Còn trống* hay
   đã *Giữ cho* nhân viên sắp onboard; tìm nhanh theo tên, tag, serial hoặc người nhận. **Chọn tài sản tận dụng vào Job** ghi
   gợi ý vào Job nháp.
3. Nút **?** cạnh tiêu đề / trường giải thích cách vận hành từng phần.

### Tồn kho (Vòng đời nhân sự)
- Danh sách mọi tài sản chưa gán người dùng theo lần đồng bộ gần nhất, chia tab **Mới**, **Thu hồi**, **Chờ thanh lý**
  (kèm số lượng), lọc theo loại, tìm theo mã tài sản / serial / tên. Mã tài sản và serial là bắt buộc: tài sản thiếu
  được đánh dấu đỏ (tab **Thiếu mã TS / serial**) để bổ sung trên SDP.
- **Thu hồi**: đồng bộ tự nhận ra tài sản lần trước đang gán cho ai đó nay về In Store (ghi người trả, ngày); Manager /
  Supervisor có thể đánh dấu tay hoặc chuyển về hàng mới.
- Chọn nhiều (ô đầu dòng, chọn tất cả theo bộ lọc) rồi **Chuyển Chờ thanh lý** / **Trả về kho** (Manager, Supervisor) hoặc
  **Thanh lý trên SDP** (chỉ **Manager** và **Administrator**, nhập `THANH LY` để xác nhận; chỉ áp dụng cho hàng đang
  Chờ thanh lý). Thay đổi chạy bằng Job nền với API key cá nhân của người bấm.
- Trên SDP cần có trạng thái tài sản cho "Chờ thanh lý" (mặc định tên `Chờ thanh lý`) và "Disposed"; đổi tên ở
  **Cấu hình module → Trạng thái “chờ thanh lý” / “đã thanh lý”**.

### Suprema BioStar 2
1. Tạo tài khoản BioStar riêng: Read = quyền xem User/Access Group; Write = Operator/Administrator có quyền quản lý User.
2. Base URL của server BioStar 2 (vd. `https://biostar.congty.local`). Nếu dùng chứng chỉ tự ký,
   tắt **Kiểm tra chứng chỉ SSL** hoặc cài CA nội bộ (khuyến nghị).
