# Triển khai Reelo lên máy nhà (cognal.xyz)

Hướng dẫn dựng toàn bộ stack Reelo trên **một máy chủ tại nhà có GPU NVIDIA**,
phục vụ qua domain **cognal.xyz** sau **Cloudflare Tunnel**, với quy trình
**deploy bằng git tag**:

> `git tag v1.2.3 && git push origin v1.2.3` → GitHub Actions build image → runner
> trên máy nhà pull image + `docker compose up -d` + chạy migration → **live**.

## Kiến trúc tóm tắt

```
Internet ──HTTPS──► Cloudflare ──tunnel──► cloudflared (máy nhà)
                                              ├─ cognal.xyz      → reelo-ui:3000  (Next.js)
                                              └─ api.cognal.xyz  → web:8000       (FastAPI)

Trong máy nhà (docker compose, mạng nội bộ "reelo"):
  web (FastAPI)  worker (Arq)  reelo-ui (Next.js)
  postgres  redis  minio (S3)  omnivoice (GPU)  cloudflared
```

- **Không mở port** trên router — toàn bộ traffic vào qua Cloudflare Tunnel.
- Image ở GHCR: `ghcr.io/tuanh2000/reelo-backend`, `reelo-ui`, `reelo-omnivoice`.
- Secret **không** nằm trong repo — chỉ ở `/opt/reelo/.env` trên máy nhà.

---

## Phần nào tự động, phần nào bạn phải tự làm

| Việc | Ai làm |
|---|---|
| Build + push image backend/UI khi tag | **Tự động** (GitHub Actions hosted) |
| Pull image + `up -d` + migrate khi tag | **Tự động** (self-hosted runner máy nhà) |
| Tạo Cloudflare Tunnel + Public Hostname + DNS | **Bạn** (Cloudflare dashboard) — mục 1 |
| Cài Docker + nvidia-container-toolkit, tạo `/opt/reelo/.env` | **Bạn** (máy nhà) — mục 2 |
| Cài self-hosted runner | **Bạn** (1 lần) — mục 3 |
| Build/pull image OmniVoice (GPU) | **Bạn** (máy nhà) — mục 4 |
| Cấu hình Google OAuth redirect/origin | **Bạn** (Google Cloud Console) — mục 5 |
| Điền secret thật vào `.env` | **Bạn** — mục 2 |

---

## 1. Cloudflare — Tunnel + Public Hostname (BẠN làm thủ công)

1. Vào **Cloudflare Zero Trust** → **Networks → Tunnels** → **Create a tunnel** →
   chọn **Cloudflared** → đặt tên (vd `reelo-home`).
2. Ở bước "Install connector", **copy TUNNEL TOKEN** (chuỗi dài bắt đầu bằng
   `eyJ...`). Lưu lại → sẽ dán vào `/opt/reelo/.env` (biến `TUNNEL_TOKEN`).
   - Ta chạy connector bằng container `cloudflared` trong compose, **không** cần
     cài cloudflared thủ công lên máy — chỉ cần token.
3. Tab **Public Hostname** của tunnel → **Add a public hostname** (thêm 2 cái):
   - **Subdomain**: (để trống) · **Domain**: `cognal.xyz` · **Service**:
     `HTTP` → `reelo-ui:3000`
   - **Subdomain**: `api` · **Domain**: `cognal.xyz` · **Service**:
     `HTTP` → `web:8000`
   - (Service trỏ tới **tên service trong compose** vì cloudflared cùng mạng
     Docker với chúng — không phải localhost.)
4. Cloudflare **tự tạo bản ghi DNS CNAME (proxied, đám mây cam)** cho 2 hostname
   trên. Nếu `cognal.xyz` đang trỏ tới **site cũ**, hai bản ghi này sẽ **đè**
   (gỡ A/CNAME cũ của `@` và `api` nếu còn xung đột). Reelo **thay** site cũ.

> Lưu ý: domain `cognal.xyz` phải đang dùng nameserver của Cloudflare (zone active).

---

## 2. Máy nhà — chuẩn bị (BẠN làm thủ công)

1. **Cài Docker Engine + Docker Compose plugin** (Linux khuyến nghị):
   theo https://docs.docker.com/engine/install/ — kiểm tra:
   ```bash
   docker --version && docker compose version
   ```
2. **Cài NVIDIA driver + nvidia-container-toolkit** (để container dùng GPU):
   ```bash
   # sau khi cài driver NVIDIA của host:
   #   https://github.com/NVIDIA/nvidia-container-toolkit  (làm theo distro)
   sudo nvidia-ctk runtime configure --runtime=docker
   sudo systemctl restart docker
   # kiểm tra GPU thấy trong container:
   docker run --rm --gpus all nvidia/cuda:12.1.0-base-ubuntu22.04 nvidia-smi
   ```
3. **Tạo thư mục cấu hình + file env thật** (đây là nơi DUY NHẤT chứa secret):
   ```bash
   sudo mkdir -p /opt/reelo
   # copy mẫu từ repo rồi điền secret thật:
   sudo cp /đường/dẫn/repo/.env.prod.example /opt/reelo/.env
   sudo nano /opt/reelo/.env       # điền tất cả CHANGE_ME_*
   sudo chmod 600 /opt/reelo/.env
   ```
   Sinh secret:
   ```bash
   openssl rand -base64 32     # cho REELO_MASTER_KEY và SESSION_SECRET (mỗi cái 1 giá trị)
   ```
   Điền: `POSTGRES_PASSWORD`, `DATABASE_URL` (đúng password vừa đặt),
   `REELO_MASTER_KEY`, `SESSION_SECRET`, `GOOGLE_OAUTH_CLIENT_ID/SECRET`,
   `MINIO_ROOT_USER/PASSWORD` (và đặt `S3_ACCESS_KEY/S3_SECRET_KEY` **bằng** cặp
   MinIO đó), `TUNNEL_TOKEN` (từ mục 1). Các URL nội bộ đã có sẵn giá trị đúng.
4. **Đăng nhập GHCR để pull được image** (cần khi image ở chế độ private):
   ```bash
   # PAT có scope read:packages (Settings > Developer settings > Tokens)
   echo "GHCR_PAT" | docker login ghcr.io -u tuanh2000 --password-stdin
   ```

> Repo cần được clone trên máy nhà (runner tự checkout khi deploy, nhưng compose
> file lấy từ checkout đó). Không cần làm gì thêm — mục 3 lo phần này.

---

## 3. GitHub — self-hosted runner trên máy nhà (BẠN làm 1 lần)

Runner này là thứ thực thi bước **deploy** ngay trên máy nhà.

1. Repo `tuanh2000/reelo` → **Settings → Actions → Runners → New self-hosted
   runner** → chọn OS (Linux) → chạy các lệnh nó in ra (download + `config.sh` +
   `run.sh`). Để **label mặc định `self-hosted`** (workflow dùng đúng label này).
2. Khuyến nghị cài runner thành **service** để tự chạy nền:
   ```bash
   sudo ./svc.sh install && sudo ./svc.sh start
   ```
3. **GHCR packages**: lần đầu image là private. Sau khi build, vào package
   `reelo-backend`/`reelo-ui` trên GitHub → **Package settings** → cho repo
   `reelo` quyền đọc (hoặc dùng `docker login` PAT như mục 2.4 — đã đủ).
4. **Không cần** thêm secret env vào GitHub: mọi secret runtime nằm ở
   `/opt/reelo/.env` trên máy. (Workflow chỉ dùng `GITHUB_TOKEN` tự có để
   login GHCR khi build/deploy.)

> Runner phải chạy từ **cùng máy** có Docker + `/opt/reelo/.env` + GPU.

---

## 4. OmniVoice — image GPU (BẠN làm, vì image rất nặng)

Image OmniVoice (CUDA + PyTorch, vài GB) **không build trên CI hosted**. Hai cách:

- **Cách A — build tại chỗ (đơn giản):** trên máy nhà, build & gắn tag rồi để
  compose dùng (đặt cùng tag bạn sẽ deploy, vd `v0.1.0`):
  ```bash
  cd /đường/dẫn/repo
  docker build -f reelo-backend/services/omnivoice/Dockerfile \
    -t ghcr.io/tuanh2000/reelo-omnivoice:v0.1.0 \
    -t ghcr.io/tuanh2000/reelo-omnivoice:latest \
    reelo-backend/services/omnivoice
  docker push ghcr.io/tuanh2000/reelo-omnivoice:v0.1.0
  docker push ghcr.io/tuanh2000/reelo-omnivoice:latest
  ```
- **Cách B — để workflow build trên runner máy nhà:** đặt repo **Variable**
  `BUILD_OMNIVOICE=true` (Settings → Secrets and variables → Actions →
  Variables). Khi tag, job `build-omnivoice` (chạy trên self-hosted) sẽ build &
  push image GPU theo đúng tag.

> **Lần đầu chạy**: model k2-fsa/OmniVoice **tải từ HuggingFace** lúc gọi `/clone`
> đầu tiên (cache vào volume `hfcache`, lần sau nhanh). `GET /health` trả OK ngay
> mà không cần model. Nếu chưa muốn dùng GPU, tạm đặt `OMNIVOICE_MOCK=1` trong
> `.env` để service trả silence (chỉ để test luồng).

---

## 5. Google OAuth (BẠN làm thủ công)

Trong **Google Cloud Console → APIs & Services → Credentials → OAuth 2.0 Client**:

- **Authorized redirect URIs**: thêm `https://api.cognal.xyz/auth/callback`
- **Authorized JavaScript origins**: thêm `https://cognal.xyz`

Dán Client ID/Secret vào `/opt/reelo/.env` (`GOOGLE_OAUTH_CLIENT_ID/SECRET`).
Hai giá trị này phải khớp đúng với redirect URI ở trên.

---

## 6. Deploy lần đầu & quy trình tag

Sau khi xong mục 1–5:

```bash
# trên máy có repo (máy nhà cũng được):
git tag v0.1.0
git push origin v0.1.0
```

Việc xảy ra **tự động**:
1. Job **build** (GitHub hosted): login GHCR → build `reelo-backend` (context =
   repo root) và `reelo-ui` (baked `NEXT_PUBLIC_API_BASE=https://api.cognal.xyz`)
   → push tag `v0.1.0` + `latest`.
2. Job **deploy** (self-hosted, máy nhà): checkout tag → login GHCR →
   `IMAGE_TAG=v0.1.0 docker compose -f docker-compose.prod.yml --env-file
   /opt/reelo/.env pull web worker reelo-ui` → `up -d --remove-orphans` →
   `run --rm web alembic upgrade head` (migration) → `ps`.

Mỗi lần phát hành mới: `git tag vX.Y.Z && git push origin vX.Y.Z` — lặp lại y hệt.
Volume (`pgdata`, `miniodata`, `redisdata`, `hfcache`) **giữ nguyên** qua mỗi
deploy nên dữ liệu không mất.

### Rollback
Tag lại commit cũ với tag mới, hoặc deploy lại tag cũ thủ công trên máy nhà:
```bash
cd /đường/dẫn/repo && git checkout vX.Y.Z   # tag muốn quay về
IMAGE_TAG=vX.Y.Z docker compose -f docker-compose.prod.yml \
  --env-file /opt/reelo/.env pull web worker reelo-ui
IMAGE_TAG=vX.Y.Z docker compose -f docker-compose.prod.yml \
  --env-file /opt/reelo/.env up -d
```
(Image cũ vẫn còn trong GHCR nên pull lại được; migration tiến chứ không lùi —
nếu cần lùi schema phải có `alembic downgrade` tương ứng.)

---

## 7. Khởi tạo & kiểm tra

- **Bucket MinIO**: service `minio-init` tự tạo bucket `${S3_BUCKET}` khi stack
  lên (chạy 1 lần rồi thoát). Kiểm tra MinIO console (nội bộ): `minio:9001`.
- **Kiểm tra health**:
  ```bash
  cd /đường/dẫn/repo
  C="docker compose -f docker-compose.prod.yml --env-file /opt/reelo/.env"
  $C ps
  $C exec web python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8000/health').read())"
  $C exec omnivoice python -c "import urllib.request;print(urllib.request.urlopen('http://localhost:8002/health').read())"
  ```
- **Từ ngoài Internet**: mở `https://cognal.xyz` (UI) và
  `https://api.cognal.xyz/health` (API trả `{"status":"ok",...}`).
- **Xem log**:
  ```bash
  $C logs -f web worker cloudflared
  ```
- **Đăng nhập**: bấm Google login trên UI → quay về `cognal.xyz` đã đăng nhập
  (cookie scope `.cognal.xyz` nên gửi được sang `api.cognal.xyz`).

---

## Lưu ý / hạn chế

- **Image OmniVoice nặng** (CUDA+torch vài GB) và **lần đầu tải model** từ
  HuggingFace lúc gọi `/clone` đầu tiên → request đầu chậm; cache vào `hfcache`.
- **GPU driver**: cần NVIDIA driver + nvidia-container-toolkit đúng phiên bản
  CUDA (image dùng CUDA 12.1). Không có GPU → đặt `OMNIVOICE_MOCK=1` (chỉ test).
- **Dung lượng GHCR**: mỗi tag tạo image mới; dọn tag/cũ định kỳ để khỏi đầy.
- **Secret**: chỉ ở `/opt/reelo/.env` (chmod 600). Repo chỉ có `.env.prod.example`.
- **Cloudflare proxied**: TLS do Cloudflare lo; container chạy HTTP nội bộ. Cookie
  `Secure` vẫn đúng vì trình duyệt thấy HTTPS.
