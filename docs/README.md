# Reelo — Technical Documents

Bộ tài liệu kỹ thuật cho phần **logic backend** của Reelo (công cụ tạo video YouTube bằng AI từ một ý tưởng). Phần UI đã hoàn thiện ở [`reelo-ui/`](../reelo-ui); các tài liệu này thiết kế phần backend lấp vào 9 stub trong [reelo-ui/lib/api.ts](../reelo-ui/lib/api.ts).

## Quyết định nền tảng (đã chốt)
- **Mô hình:** **multi-tenant SaaS** — **Google OAuth**, per-user, **BYOK** (mọi provider dùng key của user).
- **Backend:** Python **FastAPI** (web: auth + REST + enqueue) + **Arq worker (Redis)** chạy job; drive skill scripts + FFmpeg bằng subprocess trong worker.
- **Persistence:** **Postgres** (state, scope `user_id`; `spec_json` JSONB; key mã hóa AES-256-GCM) + **Object storage** (S3/GCS) cho asset `projects/<user_id>/<episode_id>/`.
- **Provider script:** **Google AI Studio official API** (BYOK free tier) — **đã bỏ gemini-web2api**. Mọi provider chính thức hỗ trợ JSON mode + system prompt.
- **Renderer:** **template đa-skill** (`skills/<id>/template.yaml`) — religion reference, story/explain/news scaffold.
- **YouTube v1:** chỉ **export `final.mp4` + SRT + thumbnail + metadata** (user tự upload).

## Quyết định Module 1 (chi tiết: xem [decisions log](module-1-ai-chatting.md#0-quy%E1%BA%BFt-%C4%91%E1%BB%8Bnh-%C4%91%C3%A3-ch%E1%BB%91t-decisions-log))
- **Ngôn ngữ thoại** chọn per series; **prompt ảnh luôn tiếng Anh**.
- **Lazy generation:** approve chỉ chốt outline + config; full script sinh per-episode lúc sản xuất (RULE+parse+validate chạy ở đây, **chunked** vì video dài).
- **Video dài, nhiều ảnh:** bỏ cap 8; số ảnh = `target_minutes × density` (Light/Standard/Dense).
- **Image style** = preset (base_prompt, registry `styles/presets.yaml`) **+** skill template (cấu trúc + tradition layer).
- **Tỉ lệ khung** 16:9/9:16 chọn per series.
- **Chỉ lưu khi approve**, **approve chốt cứng** (đổi → tạo series mới).
- **Cần sửa kèm:** reelo-ui thêm field ở màn Setup (language/target_minutes/density/aspect); skill `merge_video.py` tham số hoá khung + verify N ảnh lớn.

## Quyết định Module 2 (chi tiết: [decisions log](module-2-video-generator.md#0-quy%E1%BA%BFt-%C4%91%E1%BB%8Bnh-%C4%91%C3%A3-ch%E1%BB%91t-decisions-log))
- **Nhạc nền:** user upload per-series, **optional**, auto ducking + loop.
- **Phụ đề:** xuất **SRT riêng** kèm mp4 (không burn-in).
- **Chuyển động:** **Ken Burns** nhẹ (zoom/pan) tự động.
- **Thumbnail:** AI sinh **3 ứng viên** lúc render xong (dùng image provider), user chọn ở Review.
- **Free path trọn vẹn:** thêm **Edge-TTS** (voice free) → gemini + kie + edge không cần key.
- **Nguồn ảnh ẢNH THẬT (M2-11):** `web-commons` là provider `GENERATE_IMAGE` chính thức, **keyless** — tìm ảnh PD/CC0/CC-BY trên Wikimedia Commons theo `SegmentSpec.image_query`; lưu attribution (`credits.json`) cho publish. Khác biệt: ảnh tài liệu/khoa học thật, không phải ảnh AI.
- **Curate ảnh do CON NGƯỜI chọn (M2-12):** với provider ảnh web (`web-*`, `supports_candidates`), thay vì auto-pick hệ thống đưa **~9 ứng viên/đoạn** để user tự chọn (mặc định chọn ảnh đầu). Bước RIÊNG giữa scripted→produce (`GET/POST /episodes/{id}/image-candidates`·`/image-selection`); lưu `Episode.image_curation` (JSONB, ngoài SeriesSpec); runner tải ảnh đã chọn (`download_chosen`), thiếu chọn→fallback auto. Provider AI giữ luồng auto. Khác biệt: con người curate ảnh thật.
- **Voice video dài:** chunk TTS theo phần + concat.
- **Ảnh lỗi:** chặn render, báo user retry.
- **Orchestration:** serialize tập, song song ảnh 3–4, render=1, cảnh báo chi phí trước produce; sản xuất per-episode; giữ nguyên project folder.
- **Renderer Reelo-native** (`render.py`) thay `merge_video.py`; chỉ tái dùng client ảnh (kie) + voice (eleven) + thuật toán timing word-count.

## Quyết định Module 3 (chi tiết: [decisions log](module-3-ai-service-manager.md#0-quy%E1%BA%BFt-%C4%91%E1%BB%8Bnh-%C4%91%C3%A3-ch%E1%BB%91t-decisions-log))
- **Multi-tenant + BYOK:** key & series scope `user_id`; mọi provider dùng key của user (kể cả free tier).
- **Bỏ gemini-web2api:** script "free" = Google AI Studio official API; không sidecar.
- **Mã hóa key:** AES-256-GCM, master key từ env/secret manager (KMS-ready), per-user; **validate bằng test call khi lưu**.
- **Worker:** **Arq (Redis)** — client gọi từ worker, không in-process.
- **Usage/pricing** khai báo trong `services.yaml` + `usage_log` → cost estimate (Module 2).
- **Lớp cha `AIClient`** + `services.yaml` giữ nguyên triết lý: thêm service = 1 class + 1 khối YAML + thêm vào fallback. (Họ client mới **`web-*`**: search ảnh web + lọc license + attribution, keyless — `web-commons` là provider đầu tiên.)
- **Provider script `claude-cli` (BYO subscription):** Claude qua **subscription của chính user** (KHÔNG phải API key trả-theo-token). User tạo OAuth token bằng `claude setup-token`, dán ở Setup → lưu mã hóa per-user (key_ref `claude_oauth`); worker shell ra `claude` CLI headless với token đó (`cost_tier=free` vì Reelo không bill token). Họ client mới **`subscription-CLI`**. **Caveat ToS:** mỗi user dùng tài khoản Claude của chính họ — Reelo không cung cấp Claude, token chỉ phục vụ chính chủ. Image worker cài thêm Node + `@anthropic-ai/claude-code`. **Không** auto-fallback (chọn explicit).

## Thứ tự đọc đề xuất

| # | Tài liệu | Nội dung |
|---|---|---|
| 1 | [module-3-ai-service-manager.md](module-3-ai-service-manager.md) | **Nền tảng** — base class `AIClient`, `services.yaml` (khai báo khả năng), registry/fallback, KeyStore. Module 1 & 2 đều dùng. |
| 2 | [module-1-ai-chatting.md](module-1-ai-chatting.md) | AI Chatting — 2 pha hội thoại, RULE + parse + validate → `SeriesSpec`. |
| 3 | [module-2-video-generator.md](module-2-video-generator.md) | Video Generator — materialize `SeriesSpec` → folder skill, async job model, polling. |
| 4 | [integration.md](integration.md) | **Ghép tất cả** — topology SaaS (web + Arq worker + Postgres + Redis + OAuth + object storage), template system, persistence, luồng end-to-end, risks, thứ tự triển khai. |

## Quan hệ phụ thuộc
```
Module 3 (service manager) ◄── Module 1 (script)
        ▲                          │ SeriesSpec
        └────────────────── Module 2 (video) ◄┘
```
Module 2 phụ thuộc output của Module 1; Module 1 & 2 đều phụ thuộc Module 3 để gọi AI.

## Hợp đồng liên-module cốt lõi
- **`SeriesSpec`** (định nghĩa ở [Module 1 §5](module-1-ai-chatting.md)) — Module 1 sinh, DB lưu, Module 2 tiêu thụ.
- **`GenJob[]`** — Module 2 ghi, UI poll.
- **`services.yaml`** (Module 3) — derive ra `GET /providers` để UI không lệch với backend.

## Codebase tái sử dụng
- ~~[gemini-web2api](../gemini-web2api)~~ — **đã bỏ khỏi hệ thống SaaS** (rủi ro ToS/rate-limit khi nhiều user chung). Script dùng Google AI Studio official API (BYOK). Repo chỉ giữ cho bản self-host nếu cần.
- [skill-tao-video-Youtube-ton-giao](../skill-tao-video-Youtube-ton-giao) — tái dùng `generate_image.py` (kie) + `generate_voice.py` (eleven) + thuật toán timing word-count; bước ráp thay bằng `render.py` Reelo-native (Ken Burns/ducking/aspect — Module 2).
