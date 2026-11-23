# Cài đặt infra-ops trên Ubuntu bằng Docker

Hướng dẫn cho **1 máy chủ** Ubuntu chạy toàn bộ ứng dụng bằng Docker Compose, có HTTPS qua nginx trên máy
chủ. Kubernetes (Rancher) xem mục *Triển khai → Kubernetes* trong `README.md`.

> Các lệnh dưới đây đã được chạy thử trọn vòng (build image, khởi động, tạo quản trị viên, đăng nhập qua
> HTTPS, đồng bộ, sao lưu/khôi phục, khởi động lại `api`). Môi trường thử không phải Ubuntu thật và không có
> SDP/BioStar thật – phần kết nối hệ thống cần kiểm tra trên môi trường Test của công ty.

## 1. Thành phần

```
Người dùng ──HTTPS 443──> nginx (máy chủ, chứng chỉ công ty)
                              └─HTTP 127.0.0.1:8080──> web (SPA + proxy /api)
                                                          └──> api (FastAPI) ──> db (PostgreSQL 16)
                                                       worker (đồng bộ SDP/BioStar, chạy Job) ──┘
                                                       migrate (chạy 1 lần mỗi lần khởi động/nâng cấp)
api, worker ──HTTPS──> ServiceDesk Plus, BioStar 2 (và login.microsoftonline.com nếu bật SSO)
```

Dữ liệu nằm trong 2 Docker volume: `infra-ops_db-data` (CSDL) và `infra-ops_files` (mẫu biên bản, PDF, bản
scan đã ký, bằng chứng). Credential của SDP/BioStar được mã hoá bằng **master key** trong `.env`.

## 2. Chuẩn bị máy chủ

| Hạng mục | Khuyến nghị |
|---|---|
| Hệ điều hành | Ubuntu Server 22.04 hoặc 24.04 LTS (64-bit) |
| Cấu hình | 2 vCPU, 4 GB RAM (LibreOffice dùng khi xuất PDF), 30 GB ổ đĩa |
| Tên miền | Bản ghi DNS nội bộ, ví dụ `infraops.congty.local` |
| Chứng chỉ | Chứng chỉ TLS cho tên miền trên (CA nội bộ hoặc công cộng), dạng PEM: `fullchain.pem` + `privkey.pem` |
| Mạng vào | 443/tcp từ máy người dùng (và 22/tcp để quản trị) |
| Mạng ra (chạy) | Tới SDP và BioStar (cổng HTTPS của từng hệ thống); `login.microsoftonline.com` nếu dùng SSO |
| Mạng ra (build) | `registry-1.docker.io`, `ghcr.io`, `deb.debian.org`, `pypi.org`, `registry.npmjs.org` – nếu máy chủ không ra Internet, xem mục 10.4 |

## 3. Cài Docker Engine

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl git openssl
```

```bash
sudo install -m 0755 -d /etc/apt/keyrings
sudo curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
sudo chmod a+r /etc/apt/keyrings/docker.asc
```

```bash
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
```

```bash
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Kiểm tra (cần thấy Docker 24+ và Compose v2):

```bash
sudo docker version
sudo docker compose version
```

Các lệnh tiếp theo dùng `sudo docker …`; có thể thêm tài khoản quản trị vào nhóm `docker` thay vì dùng sudo
(nhóm này tương đương quyền root trên máy chủ).

## 4. Lấy mã nguồn

```bash
sudo git clone https://github.com/luongminhphu/infra-ops.git /opt/infra-ops
cd /opt/infra-ops
```

Repo riêng tư: dùng deploy key (chỉ đọc) của GitHub, hoặc chép bản `.zip` vào `/opt/infra-ops`.

## 5. Tạo file cấu hình `.env`

```bash
sudo cp .env.example .env
sudo chmod 600 .env
```

Sinh các giá trị bí mật (mỗi lệnh in ra một dòng để dán vào `.env`):

```bash
echo "INFRAOPS_MASTER_KEYS=v1:$(openssl rand -base64 32)"
echo "INFRAOPS_SIGNING_SECRET=$(openssl rand -hex 32)"
echo "POSTGRES_PASSWORD=$(openssl rand -hex 16)"
```

Mở `.env` (`sudo nano .env`) và điền:

| Biến | Giá trị |
|---|---|
| `INFRAOPS_ENVIRONMENT` | `production` |
| `INFRAOPS_PUBLIC_BASE_URL` | `https://infraops.congty.local` (đúng địa chỉ người dùng mở, có `https`) |
| `INFRAOPS_MASTER_KEYS` | dòng `v1:…` vừa sinh |
| `INFRAOPS_SIGNING_SECRET` | chuỗi hex vừa sinh (≥ 32 ký tự) |
| `POSTGRES_PASSWORD` | chuỗi hex vừa sinh |
| `INFRAOPS_AUTH_MODE` | `local` (tài khoản trong app) – chuyển sang `oidc` khi bật SSO Entra (mục 9.3) |
| `INFRAOPS_ENABLED_MODULES` | `lifecycle,workload` |
| `INFRAOPS_HTTP_BIND` | `127.0.0.1:8080` (chỉ nginx trên máy chủ truy cập được cổng web) |

> **Cất giữ master key ở nơi an toàn ngoài máy chủ** (két mật khẩu của phòng IT). Mất key = mất toàn bộ
> credential SDP/BioStar đã nhập (phải nhập lại); không đổi key bằng cách sửa trực tiếp – xem `.env.example`
> về cách xoay vòng key. Không đưa `.env` vào git.

## 6. Build và khởi động

```bash
sudo docker compose up -d --build
```

Lần đầu build mất 5–15 phút (tải LibreOffice, thư viện Python, Node). Kiểm tra:

```bash
sudo docker compose ps
```

Kết quả đúng: `db` *healthy*, `migrate` *exited (0)*, `api` *healthy* (sau ~30 giây), `worker` và `web`
*running*. Nếu không, xem log:

```bash
sudo docker compose logs --tail 50 migrate api worker
```

## 7. Tạo quản trị viên đầu tiên

```bash
sudo docker compose exec api python -m app.cli create-admin --username admin --name "Quản trị"
```

Nhập mật khẩu (tối thiểu 12 ký tự) hai lần. Có thể chạy lại lệnh để đặt lại mật khẩu cho tài khoản này.

## 8. HTTPS bằng nginx trên máy chủ

Phiên đăng nhập dùng cookie `Secure`: **chỉ hoạt động qua HTTPS**. Mở thẳng `http://máy-chủ:8080` sẽ đăng nhập
"thành công" nhưng bị đưa lại trang đăng nhập.

```bash
sudo apt-get install -y nginx
sudo mkdir -p /etc/nginx/tls
sudo cp fullchain.pem /etc/nginx/tls/infraops.crt
sudo cp privkey.pem /etc/nginx/tls/infraops.key
sudo chmod 600 /etc/nginx/tls/infraops.key
```

Tạo `/etc/nginx/sites-available/infra-ops`:

```nginx
server {
    listen 80;
    server_name infraops.congty.local;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name infraops.congty.local;

    ssl_certificate     /etc/nginx/tls/infraops.crt;
    ssl_certificate_key /etc/nginx/tls/infraops.key;
    ssl_protocols TLSv1.2 TLSv1.3;

    client_max_body_size 25m;   # mẫu DOCX, bản scan đã ký, bằng chứng
    add_header Strict-Transport-Security "max-age=31536000" always;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 120s;
    }
}
```

(Ubuntu 22.04/24.04 cài nginx 1.18/1.24 nên dùng `listen 443 ssl http2;`; với nginx ≥ 1.25 có thể đổi thành
`listen 443 ssl;` + `http2 on;`.)

```bash
sudo ln -s /etc/nginx/sites-available/infra-ops /etc/nginx/sites-enabled/infra-ops
sudo rm -f /etc/nginx/sites-enabled/default
sudo nginx -t
sudo systemctl reload nginx
```

Tường lửa:

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
```

Mở `https://infraops.congty.local` và đăng nhập bằng tài khoản ở mục 7.

## 9. Cấu hình trong ứng dụng

### 9.1 Kết nối hệ thống (Quản trị)

**Hệ thống → Kết nối hệ thống → Thêm kết nối** cho ServiceDesk Plus và BioStar 2, nhập credential **Read**
(đọc, đồng bộ) và **Write** (chạy Job, chuyển việc) riêng, rồi **Kiểm tra kết nối**. Chi tiết quyền cần cấp cho
từng tài khoản: `README.md` mục *Kết nối hệ thống*. Nên tạo kết nối loại **Test** trước, chạy thử Job trên
request thử rồi mới chuyển sang Production.

### 9.2 Người dùng, vai trò và module

- **Hệ thống → Người dùng & quyền**: tạo tài khoản, gán Manager / Supervisor / Operator / Chỉ xem.
- **Vòng đời → Cấu hình module**: nhãn trường RTR, ánh xạ *Loại biến động*, nhóm BioStar mặc định, người duyệt.
- **Công việc → Cấu hình công việc**: kết nối SDP, nhóm hỗ trợ, liên kết Operator ↔ technician SDP.
- **Vòng đời → Lưu đồ quy trình**: Manager rà soát checklist và bước bắt buộc bằng chứng.
- Bấm **Đồng bộ ngay** ở *Vòng đời → Đồng bộ dữ liệu* và *Công việc* để lấy dữ liệu lần đầu.

### 9.3 Đăng nhập SSO Microsoft Entra ID (tuỳ chọn)

1. Entra admin center → **App registrations → New registration**, loại *Single tenant*.
   Redirect URI (Web): `https://infraops.congty.local/api/core/auth/oidc/callback`.
2. **Certificates & secrets → New client secret**; ghi lại *Application (client) ID*, *Directory (tenant) ID*.
3. Trong `.env`: `INFRAOPS_AUTH_MODE=oidc`, `INFRAOPS_OIDC_TENANT_ID`, `INFRAOPS_OIDC_CLIENT_ID`,
   `INFRAOPS_OIDC_CLIENT_SECRET`, và `INFRAOPS_BOOTSTRAP_ADMIN_UPNS=admin.it@congty.vn` (người được cấp Quản trị
   khi đăng nhập lần đầu).
4. Áp dụng: `sudo docker compose up -d`.

Người đăng nhập SSO lần đầu có vai trò **Chỉ xem**; Quản trị gán vai trò trong app. Máy chủ cần ra được
`login.microsoftonline.com`.

## 10. Tuỳ chọn

### 10.1 SDP / BioStar dùng chứng chỉ CA nội bộ

Gộp CA công cộng của máy chủ với CA nội bộ (biến `SSL_CERT_FILE` **thay thế** kho CA mặc định, nên phải giữ cả
CA công cộng cho SSO):

```bash
cat /etc/ssl/certs/ca-certificates.crt /duong-dan/ca-noi-bo.crt | sudo tee /opt/infra-ops/ca-bundle.crt > /dev/null
```

Tạo `/opt/infra-ops/docker-compose.override.yml` (Compose tự đọc file này):

```yaml
services:
  api:
    volumes: ["./ca-bundle.crt:/etc/infraops/ca-bundle.crt:ro"]
    environment: { SSL_CERT_FILE: /etc/infraops/ca-bundle.crt }
  worker:
    volumes: ["./ca-bundle.crt:/etc/infraops/ca-bundle.crt:ro"]
    environment: { SSL_CERT_FILE: /etc/infraops/ca-bundle.crt }
```

```bash
sudo docker compose up -d
```

Chỉ tắt *Kiểm tra chứng chỉ SSL* trong kết nối khi thử nghiệm.

### 10.2 Font Aptos cho biên bản PDF

Mẫu biên bản dùng font Aptos (Microsoft). Chép các file `.ttf` vào `/opt/infra-ops/fonts/` và thêm vào
`docker-compose.override.yml` của dịch vụ `api` (và `worker`): `- ./fonts:/usr/share/fonts/custom:ro`, rồi
`sudo docker compose up -d`. Không có Aptos, PDF dùng font thay thế (Inter / DejaVu).

### 10.3 Truy cập thử không có HTTPS (chỉ để thử nghiệm)

Thêm `INFRAOPS_SESSION_COOKIE_SECURE=false` và `INFRAOPS_HTTP_BIND=8080` vào `.env`, đặt
`INFRAOPS_PUBLIC_BASE_URL=http://<ip>:8080`, `sudo docker compose up -d`. Không dùng cho môi trường thật.

### 10.4 Máy chủ không ra được Internet

Build ở máy có Internet rồi chuyển image sang:

```bash
sudo docker compose build
sudo docker save infra-ops-api infra-ops-web postgres:16-alpine -o infra-ops-images.tar
```

Trên máy chủ (đã có mã nguồn và `.env`):

```bash
sudo docker load -i infra-ops-images.tar
sudo docker tag infra-ops-api infra-ops-worker
sudo docker tag infra-ops-api infra-ops-migrate
sudo docker compose up -d --no-build
```

## 11. Vận hành

Tên image/volume có tiền tố là tên thư mục cài đặt (`infra-ops` khi cài vào `/opt/infra-ops`); nếu đổi thư
mục, đổi tiền tố trong các lệnh bên dưới cho khớp (`sudo docker volume ls`).

### Xem trạng thái và log

```bash
sudo docker compose ps
sudo docker compose logs -f --tail 100 api worker
```

Các dịch vụ tự khởi động lại khi lỗi và khi máy chủ khởi động (`restart: unless-stopped`).

### Nâng cấp phiên bản

```bash
cd /opt/infra-ops
sudo git pull
sudo docker compose up -d --build
```

Migration CSDL chạy tự động (dịch vụ `migrate`) trước khi `api`/`worker` khởi động. Sao lưu trước khi nâng cấp.

### Sao lưu

```bash
sudo mkdir -p /var/backups/infra-ops
cd /opt/infra-ops
sudo docker compose exec -T db pg_dump -U infraops -Fc infraops | sudo tee /var/backups/infra-ops/db-$(date +%F).dump > /dev/null
sudo docker run --rm -v infra-ops_files:/data:ro -v /var/backups/infra-ops:/backup alpine tar czf /backup/files-$(date +%F).tgz -C /data .
```

Sao lưu hằng ngày bằng cron (`sudo crontab -e`), ví dụ 01:30:

```cron
30 1 * * * cd /opt/infra-ops && docker compose exec -T db pg_dump -U infraops -Fc infraops > /var/backups/infra-ops/db-$(date +\%F).dump && docker run --rm -v infra-ops_files:/data:ro -v /var/backups/infra-ops:/backup alpine tar czf /backup/files-$(date +\%F).tgz -C /data . && find /var/backups/infra-ops -mtime +30 -delete
```

Chép thư mục sao lưu ra máy khác. Bản sao lưu chỉ dùng được với **đúng master key** trong `.env`.

### Khôi phục

```bash
cd /opt/infra-ops
sudo docker compose stop api worker
sudo docker compose exec -T db pg_restore -U infraops -d infraops --clean --if-exists < /var/backups/infra-ops/db-2026-11-23.dump
sudo docker run --rm -v infra-ops_files:/data -v /var/backups/infra-ops:/backup alpine sh -c "rm -rf /data/* && tar xzf /backup/files-2026-11-23.tgz -C /data"
sudo docker compose start api worker
```

## 12. Sự cố thường gặp

| Hiện tượng | Nguyên nhân / cách xử lý |
|---|---|
| Đăng nhập xong lại quay về trang đăng nhập | Đang mở bằng `http://`. Dùng HTTPS (mục 8). |
| `migrate` thoát mã khác 0, log báo `INFRAOPS_MASTER_KEYS is required` / `SIGNING_SECRET must be at least 32` | Thiếu hoặc sai giá trị trong `.env` (mục 5). Sửa rồi `sudo docker compose up -d`. |
| *Đồng bộ ngay* không có kết quả | `worker` không chạy: `sudo docker compose ps`, `sudo docker compose logs worker`. |
| Đồng bộ báo "Chưa cấu hình kết nối cho hệ thống sdp" | Chưa tạo/bật kết nối SDP hoặc chưa chọn trong Cấu hình module. |
| Kiểm tra kết nối lỗi SSL | SDP/BioStar dùng CA nội bộ: mục 10.1. |
| Tải file lên báo lỗi 413 | Tăng `client_max_body_size` trong nginx máy chủ (mặc định ứng dụng nhận tối đa 20 MB). |
| Trang báo lỗi 502 | `api` đang khởi động (chờ *healthy*) hoặc lỗi: `sudo docker compose logs api`. |
| PDF biên bản sai font | Thiếu font Aptos: mục 10.2. |
