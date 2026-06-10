# Reelo — Agent Team Charter

Team 4 agent triển khai backend Reelo (multi-tenant SaaS). Mỗi agent sở hữu một mảng; **platform-lead** giữ contract chung. Định nghĩa agent: `.claude/agents/*.md`.

## Roster & ownership

| Agent | Sở hữu | Doc |
|---|---|---|
| **reelo-platform-lead** | Hạ tầng dùng chung: FastAPI app, Google OAuth, Postgres + migrations, Redis + Arq, object storage, models chung, wiring UI, tracer-bullet. **Giữ contract liên-module.** | [integration.md](integration.md) |
| **reelo-ai-services** | Module 3: `AIClient` + clients, `services.yaml`, registry/fallback, `KeyStore` (BYOK, mã hóa), usage/pricing. | [module-3-ai-service-manager.md](module-3-ai-service-manager.md) |
| **reelo-scriptwriting** | Module 1: wizard chat, RULE+structured-output+validate, `SeriesSpec`, lazy episode script gen, style. | [module-1-ai-chatting.md](module-1-ai-chatting.md) |
| **reelo-video-generator** | Module 2: materialize, Arq produce job, `render.py` (Ken Burns/ducking/aspect), SRT, thumbnail, voice chunk+concat, upload. | [module-2-video-generator.md](module-2-video-generator.md) |

## Đồ thị phụ thuộc
```
platform-lead (nền + contract)
      │ cung cấp app/DB/Arq/storage/models chung
      ▼
reelo-ai-services (Module 3) ◄── reelo-scriptwriting (Module 1)
      ▲                               │ SeriesSpec
      └──────────── reelo-video-generator (Module 2) ◄┘
```
- Module 2 phụ thuộc output Module 1; Module 1 & 2 gọi AI qua Module 3; cả 3 dựa nền của platform-lead.

## Build order
1. **platform-lead** — skeleton + interface chung (models Pydantic/ORM, ABC `AIClient`, Arq khung, storage adapter, OAuth). Mở khóa song song.
2. **reelo-ai-services** — Module 3 + **stub client** (vd trả JSON hợp lệ) để M1/M2 không bị chặn.
3. **reelo-scriptwriting** — Module 1 (dùng stub client khi cần).
4. **reelo-video-generator** — Module 2 (dùng stub client + asset mẫu).
5. **platform-lead** — wire `reelo-ui/lib/api.ts` + Setup fields + Google login → **tracer-bullet** end-to-end (integration §8).

Sau bước 1, các module code song song trên interface chung.

## Contract liên-module (đổi PHẢI qua platform-lead)
Để 3 module không drift, mọi thay đổi các contract sau phải được platform-lead duyệt + cập nhật doc + báo module liên quan:
- **`SeriesSpec` / `EpisodeSpec` / `SegmentSpec`** (M1 sinh → M2 tiêu thụ).
- **`GenJob[]`** + endpoint `/generation/*` (M2 ghi → UI poll).
- **`AIClient` ABC + DTO**, schema **`services.yaml`**, shape `GET /providers`, **`KeyStore`/`CallContext`** (M3).
- **DB models** dùng chung + shape REST của 9 hàm `reelo-ui/lib/api.ts`.

Quy trình: module đề xuất đổi → platform-lead đánh giá ảnh hưởng → cập nhật model + doc → thông báo → các module điều chỉnh.

## Nguyên tắc chung cho mọi agent
- **Đọc doc trước khi code**; doc là nguồn sự thật. Lệch doc → cập nhật doc (qua platform-lead) hoặc hỏi lại.
- **Chỉ sửa code mảng mình sở hữu**; mảng khác chỉ đọc để hiểu contract.
- **Không gọi API thật khi test** — dùng stub/mlock; viết test cho phần logic.
- **BYOK + multi-tenant**: mọi thứ scope `user_id`; không hardcode key; không log/return plaintext key.
- Báo cáo cuối: file đã tạo, contract công khai, cách chạy test, phần còn stub/phụ thuộc.

## Cách gọi (từ main agent)
- Giao việc: spawn agent theo tên (vd `reelo-ai-services`) với mô tả task cụ thể.
- Các agent độc lập có thể chạy song song sau khi platform-lead dựng xong interface chung (bước 1).
- Việc đụng nhiều module → giao **platform-lead** điều phối.
