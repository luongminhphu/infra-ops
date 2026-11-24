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
| 3 | Biên bản Bàn giao / Thu hồi theo mẫu thật: mẫu có phiên bản, số tự sinh, PDF, tải bản ký, đính kèm SDP | Xong |
| 4 | Workflow Onboard / Offboard từ request SDP (gán/thu hồi tài sản, BioStar, biên bản, đóng request) | Xong |
| 5 | Vai trò Manager / Supervisor / Operator / Viewer; module **Công việc Helpdesk**: task SDP theo Operator, chuyển việc (ghi sang SDP), thống kê theo kỳ | Xong |
| 6 | Lưu đồ Onboard/Offboard, checklist từng bước, Operator xác nhận kèm bằng chứng | Xong |
| 7 | Giao diện “Phòng điều khiển”: rail phân hệ + menu con, màu cobalt, font Inter, cho toàn app | Xong |
| 8 | **Việc của tôi** cho Operator (task SDP theo trạng thái, Job onboard/offboard, việc cần chuẩn bị), tự tạo tài khoản Operator, Job theo technician request RTR + chuyển Job (ghi sang SDP), lưu đồ swimlane theo người, đề xuất mua thiết bị | Xong |
| 9 | **PO tổng** theo hạn mức (mặc định 200 triệu, tự mở PO tiếp theo), bảng giá do Supervisor cập nhật, bằng chứng Function Head, **Chốt PO** → email nháp .eml; **Kho & cấp phát**: band trong RTR × tiêu chuẩn cấp phát × tài sản In Store → tận dụng hoặc đề xuất mua; tooltip hướng dẫn | Xong |
| 10 | **Nhận hàng → nhập kho SDP**: nhập serial/asset tag, worker tạo tài sản In Store trên SDP (không tạo trùng serial), Kho & cấp phát đồng bộ lại để gợi ý cho onboard | **Xong** |
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

### docker-compose (1 máy chủ Ubuntu)

Hướng dẫn đầy đủ (cài Docker, `.env`, HTTPS bằng nginx, SSO, CA nội bộ, sao lưu/khôi phục, nâng cấp, sự cố):
**[docs/install-ubuntu-docker.md](docs/install-ubuntu-docker.md)**. Tóm tắt:

```bash
cp .env.example .env     # điền INFRAOPS_MASTER_KEYS, INFRAOPS_SIGNING_SECRET, POSTGRES_PASSWORD, INFRAOPS_PUBLIC_BASE_URL
docker compose up -d --build
docker compose exec api python -m app.cli create-admin --username admin --name "Quản trị"
```

Compose chạy `db`, `migrate` (một lần), `api`, `worker` (đồng bộ SDP/BioStar, chạy Job) và `web` (cổng 8080,
đặt `INFRAOPS_HTTP_BIND=127.0.0.1:8080` khi đứng sau nginx HTTPS trên máy chủ). Đăng nhập cần HTTPS vì cookie
phiên có cờ `Secure`.

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

Vai trò được gán hoàn toàn trong app (Người dùng & quyền). Người đăng nhập SSO lần đầu là **Chỉ xem**.

| Vai trò | Quyền chính |
|---|---|
| Quản trị | Toàn quyền, cấu hình kết nối hệ thống và phân quyền |
| Manager | Quyền của Supervisor + cấu hình module (Vòng đời, Công việc, mẫu biên bản), buộc đóng Job, xem nhật ký |
| Supervisor | Quyền của Operator + xem task của mọi Operator và điều phối/chuyển task (ghi sang SDP). Không cấu hình hệ thống/module, không quản lý người dùng |
| Operator | Thực hiện các task trên app (Job onboard/offboard, biên bản, đồng bộ); **chỉ thấy task và thống kê của mình** |
| Chỉ xem | Chỉ đọc (thấy task của mọi Operator) |

Kết nối hệ thống và người dùng/phân quyền chỉ Quản trị thay đổi. Quản trị có thể chỉnh quyền từng vai trò trong ma trận phân quyền.

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
   - **Bảng giá**: Supervisor nhập/cập nhật đơn giá sau mỗi lần đối tác báo giá.
   - **Duyệt** (Supervisor/Manager) cần bằng chứng và thiết bị có giá; đề xuất đã duyệt tự vào **PO tổng** đang mở
     theo thứ tự duyệt, PO không đủ chỗ dưới **hạn mức** (Cấu hình công việc, mặc định 200.000.000 đ) thì hệ thống
     tự mở PO tiếp theo. Đổi giá/hạn mức thì các PO nháp được gom lại.
   - **Chốt PO**: cấp số `PO-<năm>-<STT>`, khoá đơn giá, chuyển đề xuất sang *Đã đặt mua* và tải file **.eml** – mở
     bằng Outlook là email nháp theo mẫu (Cấu hình công việc), kèm Excel chi tiết và bằng chứng. Kiểm tra rồi gửi.
   - Khi hàng về: Supervisor bấm **Nhận hàng**. Thiết bị có **Sản phẩm trên SDP** trong bảng giá: nhập serial (bắt
     buộc) và asset tag từng chiếc → worker tạo tài sản *In Store* trên SDP bằng credential Write (serial đã có trên
     SDP thì giữ nguyên, lỗi giữa chừng bấm **Thử lại** an toàn), xong đề xuất thành *Đã nhận hàng* và **Kho & cấp
     phát** được đồng bộ lại để gợi ý tận dụng cho nhân viên sắp onboard. Linh kiện/sửa chữa (không có sản phẩm SDP)
     chỉ đánh dấu đã nhận.
   - **Xuất Excel** toàn bộ đề xuất (đơn giá, thành tiền, PO).

### Việc của tôi (Operator)
Trang **Việc của tôi** (rail trái / thanh tab trên điện thoại) gom mọi việc của người đang đăng nhập:
- **Onboard / Offboard:** Job có request RTR giao cho bạn trên SDP, chia *Cần bạn xử lý ngay* (bước chờ xác nhận,
  lỗi), *Đang chạy*, *Sắp tới – cần chuẩn bị* (kèm danh sách việc chuẩn bị: chọn tài sản, kiểm tra user SDP…) và
  *Đã xong gần đây*.
- **Task Helpdesk:** đang thực hiện / chưa bắt đầu / tạm dừng / quá hạn / xong 7 ngày qua, và gợi ý đề xuất mua thiết bị.

Người phụ trách Job lấy theo **technician của request RTR** trên SDP (đồng bộ lại mỗi lần sync). Manager /
Supervisor bấm **Chuyển Job** trên trang Job: worker đổi technician của request trên SDP rồi ghi lịch sử phụ trách.
**Lưu đồ** của Job vẽ mỗi người phụ trách một làn; mũi tên nhảy làn đánh dấu lúc chuyển giao (giờ, trên SDP hay
trong infra-ops).

### Kho & cấp phát (Vòng đời nhân sự)
1. **Cấu hình module → Tiêu chuẩn cấp phát thiết bị theo band**: mỗi dòng là một thiết bị (band áp dụng, loại tài
   sản SDP, model chứa – tuỳ chọn, số lượng, tên trong bảng giá). Band lấy từ dòng `Band:` trong request RTR (đổi
   nhãn ở mục Request RTR); thiếu band thì Operator nhập tay trên trang Kho & cấp phát.
2. **Kho & cấp phát** dùng tài sản In Store, chưa gán user, của lần đồng bộ gần nhất: nhân viên có ngày hiệu lực
   sớm hơn được ưu tiên; tài sản đã chọn trên Job nháp giữ cho Job đó. Mỗi dòng hiện *Tận dụng* (tài sản cụ thể) hoặc
   *Cần mua* → **Lập đề xuất mua** (điền sẵn thiết bị, số lượng, người dùng). **Chọn tài sản tận dụng vào Job** ghi
   gợi ý vào Job nháp.
3. Nút **?** cạnh tiêu đề / trường giải thích cách vận hành từng phần.

### Suprema BioStar 2
1. Tạo tài khoản BioStar riêng: Read = quyền xem User/Access Group; Write = Operator/Administrator có quyền quản lý User.
2. Base URL của server BioStar 2 (vd. `https://biostar.congty.local`). Nếu dùng chứng chỉ tự ký,
   tắt **Kiểm tra chứng chỉ SSL** hoặc cài CA nội bộ (khuyến nghị).
