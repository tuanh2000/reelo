# Integration — Ráp 3 module thành Reelo (multi-tenant SaaS)

> Tài liệu "ghép" cuối cùng: kiến trúc tổng, vòng đời process, auth, template system, persistence, luồng end-to-end, risks. Đọc sau khi nắm [Module 3](module-3-ai-service-manager.md) → [Module 1](module-1-ai-chatting.md) → [Module 2](module-2-video-generator.md).

> **Bối cảnh đã chốt:** Reelo là **multi-tenant SaaS** — Google OAuth, per-user, **Postgres**, **Redis + Arq worker**, **BYOK** (mọi provider dùng key của user), **bỏ hẳn gemini-web2api** (script dùng Google AI Studio official API). Điều này thay cho thiết kế local-first/SQLite/sidecar ở các bản nháp trước.

---

## 1. Kiến trúc tổng & topology

```
                ┌────────────────┐   HTTPS    ┌─────────────────────────────────┐
 Browser ─────► │  reelo-ui      │ ─────────► │  Reelo API (FastAPI web)          │
 (Google login) │  Next.js (SSR) │ ◄───────── │   ├─ Google OAuth / session        │
                └────────────────┘  poll jobs │   ├─ REST (9 hàm api.ts, per-user) │
                                              │   ├─ enqueue Arq job → Redis        │
                                              │   └─ ServiceRegistry (Module 3)     │
                                              └───────┬───────────────┬─────────────┘
                                                      │ enqueue       │ read state
                                                      ▼               ▼
                                              ┌──────────────┐  ┌──────────────────┐
                                              │ Redis (Arq)  │  │ Postgres          │
                                              └──────┬───────┘  │ users, series,    │
                                                     │ pull job │ episodes, jobs,   │
                                                     ▼          │ api_keys(enc),    │
                                              ┌──────────────┐  │ usage_log         │
                                              │ Arq workers  │  └──────────────────┘
                                              │  - Module 1  │  HTTP ──► OmniVoice service (GPU)
                                              │  - Module 2  │  subprocess: generate_image.py / generate_voice.py
                                              │  - Module 3  │  + render.py (ffmpeg) + EdgeTTS (pure-py)
                                              └──────┬───────┘
                                                     ▼  assets
                                          Object Storage (S3/GCS): projects/<user>/<episode>/...
                                          + voice-samples/<user>/<series>/sample.wav (mẫu clone)
                                          (web serve final.mp4 / thumbnail qua signed URL)
```

- **OmniVoice microservice (GPU, tùy chọn):** voice-clone (k2-fsa) là model GPU → chạy ở **service riêng** `reelo-backend/services/omnivoice/` (FastAPI, host GPU CUDA), KHÔNG nạp vào worker. Worker gọi qua `OMNIVOICE_URL` khi `voice.provider=omnivoice`. Đây là service Reelo-hosted (không BYOK); không cấu hình URL → provider tự ẩn (graceful). Smoke test không GPU: chạy service với `OMNIVOICE_MOCK=1` (trả silence 24kHz).

- **Web (FastAPI):** auth, REST, enqueue job, đọc trạng thái để poll. Không chạy job nặng.
- **Worker (Arq):** chạy Module 1 (lazy script) + Module 2 (assets + render) + gọi Module 3 client. I/O-bound (gọi API) + subprocess (skill/ffmpeg).
- **Postgres:** state đa-người-dùng. **Redis:** hàng đợi Arq (+ có thể cache/ratelimit). **Object storage:** asset lớn (vì nhiều worker không chia sẻ local disk).
- **Không sidecar gemini-web2api** (đã bỏ — [Module 3 M3-3](module-3-ai-service-manager.md)).

**Layout backend đề xuất:**
```
reelo-backend/
├── web/                    # FastAPI: auth, routers, enqueue
├── worker/                 # Arq: task định nghĩa (produce_episode, generate_script, retry_child)
├── clients/                # base.py, gemini.py, openai_compat.py, anthropic.py, skill_voice.py, skill_image.py, edge_tts.py, commons_image.py (web-commons, ẢNH THẬT keyless), pexels_video.py (web-pexels, CLIP THẬT BYOK)
├── registry.py  keystore.py  usage.py
├── module1/  module2/      # logic script + materialize/render/subtitles/thumbnail
├── models/                 # SeriesSpec..., ORM models
├── db/                     # Postgres (SQLModel/SQLAlchemy + Alembic), repository
├── storage/                # object storage adapter (S3/GCS) + local temp
├── skills/<id>/template.yaml   styles/presets.yaml   services.yaml
└── config.py
```

---

## 2. Vòng đời process & I/O

### Arq workers
- Web `enqueue_job("produce_episode", user_id, series_id, episode_id)` → trả `jobId` ngay.
- Worker pull job, dựng `CallContext{user_id, KeyStore, UsageLogger}`, chạy pipeline, cập nhật bảng `gen_job` (Postgres) để web poll.
- Concurrency = số worker × concurrency/worker (giới hạn tự nhiên; không quota per-user v1).

### Skill scripts + FFmpeg (subprocess trong worker)
Helper chung — chỉ còn dùng cho `generate_image.py` (kie) và `generate_voice.py` (eleven):
```python
async def run_skill_script(script, args, env=None) -> dict:
    proc = await asyncio.create_subprocess_exec(sys.executable, str(SKILL_ROOT/"scripts"/script), *args,
        stdout=PIPE, stderr=PIPE, cwd=SKILL_ROOT, env={**os.environ, **(env or {})})
    out, err = await proc.communicate()
    if proc.returncode != 0: raise SubprocessError(script, proc.returncode, err.decode())
    return json.loads(out.decode())
```
- Bơm **key BYOK của user** qua `env=` (`keys.as_env(user_id, ...)`), just-in-time.
- **Ráp video là Reelo-native `render.py`** (Ken Burns + ducking + aspect + N ảnh — [Module 2 §5/§15](module-2-video-generator.md)), **thay** `merge_video.py`; cũng gọi `ffmpeg`. Voice free `EdgeTTSClient` pure-Python (keyless).
- **Asset:** worker ghi vào **local temp** trong lúc render → upload lên **object storage** (`projects/<user_id>/<episode_id>/...`) → web phục vụ `final.mp4`/thumbnail qua **signed URL**. DB lưu key/URL, không lưu bytes.

---

## 3. Template system đa-skill (Module 1 + 2)

`reelo-backend/skills/<id>/template.yaml` — religion là reference, story/explain/news scaffold.

```yaml
id: religion
display_name: "Tôn giáo & Lịch sử"
script:
  structure: [hook, context, layer_literal, layer_theological, layer_practical, closing]
  word_ratios: { hook: 0.08, context: 0.18, layers: 0.60, closing: 0.14 }
  rule_prompt_extra: "…định hướng riêng, nối vào system prompt khi sinh script…"
image:
  recommended_preset: "painterly-devotional"
  style_layers: { western_christian: "...", buddhism_theravada: "...", islam: "..." }
  default_aspect: "16:9"
voice:
  default_voice_id: "JBFqnCBsd6RMkjVDRZzb"
  settings: { stability: 0.55, similarity_boost: 0.75, style: 0.35, speed: 0.92 }
```

| Field | Module 1 | Module 2 |
|---|---|---|
| `script.*` (`structure`, `word_ratios`, `rule_prompt_extra`) | ✓ định hướng + chunk + nối system prompt | |
| `image.style_layers`, `recommended_preset` | gắn vào `image_style.style_layer` | ✓ prepend image_prompt |
| `image.default_aspect` | default (user override per series — D8) | ✓ `--size` + khung render |
| preset `base_prompt`/`palette` (registry `styles/presets.yaml`, D4) | gắn `image_style.base_prompt` | ✓ prepend đầu image_prompt |
| `voice.*` | default `VoiceConfig` | ✓ truyền TTS |

> Số ảnh suy từ `target_minutes × density` (D5), không từ template; `max_images` đã bỏ (D3).

### Presets style (`styles/presets.yaml`) — D4
6 preset UI (cinematic/documentary/animated/minimal/vintage/noir) + `painterly-devotional` (BASE STYLE từ skill cũ), mỗi cái 1 `base_prompt` EN + `palette`. `image_prompt` cuối = `preset.base_prompt` + `skill.style_layers[tradition]?` + `segment.image_prompt`.

---

## 4. Auth (Google OAuth — multi-tenant)

- **Login = Google OAuth.** User có tài khoản riêng; mọi `series/episode/api_keys/usage` scope theo `user_id`.
- Session (cookie) ở web; mọi REST endpoint yêu cầu user đã đăng nhập; worker nhận `user_id` qua job payload.
- **OAuth login ≠ BYOK key.** Login là Google account; key là token các AI service user tự lấy (lưu mã hóa per-user — [Module 3 §7](module-3-ai-service-manager.md)).
- reelo-ui cần thêm luồng đăng nhập Google (hiện chưa có) — xem risks.

---

## 5. Persistence

### Postgres (state đa-người-dùng)
| Bảng | Nội dung |
|---|---|
| `user` | từ Google OAuth (id, email, …) |
| `series` | scope `user_id`; `spec_json` (JSONB chứa `SeriesSpec`) |
| `episode` | `user_id`, `status`, paths/URL asset, `image_curation` (JSONB, M2-12/M2-13: ứng viên media web — ẢNH + CLIP — gộp nhiều nguồn `web-*` + lựa chọn người dùng — NGOÀI SeriesSpec) |
| `api_keys` | `user_id`, `key_ref`, **ciphertext+nonce** (AES-256-GCM, master key env/KMS — M3-4) |
| `usage_log` | `user_id`, provider, task, units, cost, ts (M3-6) |
| `gen_job` | parent/child job cho polling (state, progress, stderr, parent_id, `user_id`) |

Repository layer tách sạch. JSONB cho `spec_json` (document, query khi cần).

### Object storage (asset lớn)
- `projects/<user_id>/<episode_id>/{script.md, images/, voice/, music/, subs.srt, thumbnails/, final.mp4, credits.json?}`.
- `voice-samples/<user_id>/<series_id>/sample.wav` — mẫu giọng clone OmniVoice (wav 24kHz mono, chuẩn hóa khi upload). `POST /series/{id}/voice-sample` (audio+transcript+language) validate 3–30s → lưu key này → set `series.spec_json.voice = {provider:"omnivoice", mode:"clone", voice_sample:{audio_key, transcript, language}}`. Lúc produce, worker tải sample về temp rồi gửi kèm mỗi chunk tới OmniVoice.
  - `credits.json` chỉ có khi dùng provider media web (web-commons / web-pexels): list attribution per-media `{title, author, license, source_url, media_type}` — bắt buộc hiển thị credit khi publish (M2-11/M2-13).
- Worker render ở local temp → upload object storage → DB lưu key/URL → web trả signed URL.
- (Self-host nhỏ có thể dùng shared volume thay object storage; adapter `storage/` che khác biệt.)

---

## 6. Luồng end-to-end (per-user)

```
0. Login       Google OAuth → session (user_id)
1. Ý tưởng     wizard → POST /wizard/message → ServiceRegistry.resolve(WRITE_SCRIPT, provider, user)
               → provider chính thức (BYOK key); TA tự gửi history (messages[]); AI hỏi thêm; outline preview
2. Config      Setup: skill, providers + NHẬP KEY (BYOK), language, target_minutes, density, aspect, upload nhạc
               Style: chọn preset / POST /style/infer
3. Approve     POST /wizard/approve → build SeriesSpec shell (outline đã sửa + config); segments=[]; status=draft
               → lưu series (Postgres, scope user_id)
4. Lazy script user mở tập → enqueue generate_script(user,episode) → worker:
               derive segment_count(target,density) → chunked structured-output (JSON mode native) → segments + youtube meta
               → status scripted (Module 1)
4b. Chọn media (CHỈ provider media web `web-*` / `web`, M2-12+M2-13) — bước RIÊNG scripted→produce:
               GET /episodes/{id}/image-candidates → lưới gộp ẢNH (web-commons) + CLIP (web-pexels nếu có key) /đoạn (cache image_curation, mặc định chọn mục đầu = ảnh)
               user chọn 1 media/đoạn → POST /episodes/{id}/image-selection {selections:{idx:candidate_id}}
               (provider AI → 409, bỏ qua bước này, produce thẳng; không key Pexels → lưới chỉ ảnh)
5. Generate    workspace produce → enqueue produce_episode(user,episode) → worker:
               materialize → temp → voice(chunk+concat, eleven|edge) ∥ N×media(kie auto | web download_chosen: ảnh→raster, clip→.mp4) → render.py (ảnh KenBurns | clip fit/loop/mute + duck + aspect)
               → subs.srt + 3 thumbnail → upload object storage
               → web poll GET /generation/{jobId} → GenJob[]
6. Export      POST /publish/export → {signed videoUrl, srtUrl, thumbnailUrl, metadata}  (v1: user tự upload YouTube)
```

### Map 9 hàm UI → endpoint
| Hàm [api.ts](../reelo-ui/lib/api.ts) | Endpoint | Module |
|---|---|---|
| `listSeries`/`saveSeries` | `GET`/`POST`/`PUT /series` (per-user) | 1 |
| `sendWizardMessage` | `POST /wizard/message` | 1 |
| (approve) | `POST /wizard/approve` | 1 |
| (mở tập / produce) | `POST /episodes/{id}/script` (enqueue) | 1 |
| `inferStyle` | `POST /style/infer` | 1 |
| `getImageCandidates`/`saveImageSelection` | `GET`/`POST /episodes/{id}/image-candidates`·`/image-selection` (web media: ảnh+clip, M2-12/M2-13) | 2 |
| `startGeneration` | `POST /generation/start` (enqueue Arq) | 2 |
| `pollGeneration` | `GET /generation/{jobId}` | 2 |
| (upload nhạc) | `POST /series/{id}/music` | 2 |
| `publishToYouTube` | `POST /publish/export` | 2 |
| `saveApiKey` | `POST /keys` (validate + mã hóa, per-user) | 3 |
| (init providers) | `GET /providers` | 3 |

---

## 7. Risks & open questions

1. **BYOK UX.** User phải tự lấy key (Google AI Studio free, kie, ElevenLabs…). Cần `key_help_url` + hướng dẫn rõ; provider free vẫn cần key (trừ Edge-TTS). reelo-ui phải đổi PROVIDERS (tách `cost_tier`/`requires_key`/`key_help_url`).
2. **Key skill `.env` vs KeyStore.** Worker bơm key BYOK qua `env=` subprocess. Verify `load_dotenv` không override env đã set; nếu có → ghi `.env` riêng theo job (temp).
3. **Sync ảnh↔thoại xấp xỉ** (±1-3s). Chấp nhận v1.
4. **Render dài + N ảnh lớn** (D3) + Ken Burns (M2-3): verify render-by-clip+xfade với N lớn; fallback batch+concat. Worker render CPU-nặng → giới hạn concurrency render/worker.
5. **Nội dung style/skill cần điền (D4).** `styles/presets.yaml` (base_prompt EN cho 6 preset + painterly-devotional) + story/explain/news template.
6. **YouTube auto-upload (roadmap).** OAuth Google đã có (login) → có thể xin thêm scope YouTube sau; v1 chỉ export.
7. **Master key / KMS (M3-4).** v1 master key từ env; production nâng KMS + quy trình rotation/re-encrypt.
8. **Không quota v1 (M3-8).** 1 user có thể chiếm nhiều worker → cần fair-scheduling/quota sớm sau v1 (DoS nội bộ).
9. **reelo-ui đổi nhiều:** thêm Google login, field Setup (language/target_minutes/density/aspect/upload nhạc), ô key cho provider free, link lấy key.
10. **Object storage:** chọn S3/GCS/MinIO; signed URL; dọn asset cũ (chi phí lưu trữ).
11. **Edge-TTS keyless dùng chung IP server** → có thể bị Microsoft rate-limit ở quy mô; theo dõi, cần thì proxy.

---

## 8. Verification (kiểm chứng thiết kế)
1. **Đối chiếu schema:** mọi field UI ([data.ts](../reelo-ui/lib/data.ts)) có nguồn trong `SeriesSpec`/`GenJob`; 9 hàm [api.ts](../reelo-ui/lib/api.ts) có endpoint (§6).
2. **Đối chiếu invariant skill:** `count(===) == count(*.png) == len(segments)`; tên file zero-padded khớp thứ tự ([merge_video.py](../skill-tao-video-Youtube-ton-giao/scripts/merge_video.py) — nay là render.py kế thừa).
3. **Tracer-bullet (code đầu tiên):** 1 user login → nhập key Google AI Studio → ý tưởng → `/wizard/message` → approve → lazy script (JSON mode) → enqueue produce → worker materialize → image(kie)+voice(edge) → render.py → upload → signed URL final.mp4. Chạy thông end-to-end = thiết kế đúng. Milestone #1.
   - ✅ **DONE** — `reelo-backend/tests/test_tracer_bullet.py`. Chạy **không cần key thật / Google login thật**: dùng `stub-script`/`stub-voice`/`stub-image` (keyless) + auth override. Drive `build_series_spec → generate_episode_script → run_produce_episode` qua fake DB in-memory + `LocalObjectStorage` thật; **render bằng ffmpeg thật** ra `final.mp4` (h264+aac, ffprobe đọc được) + `subs.srt` + 3 thumbnail, episode `status=assembled`, rồi `signed_url` (đúng cái `/publish/export` trả). Thêm 2 test HTTP/worker chạy inline (TestClient + gọi thẳng task) chứng minh endpoint nối đúng. Chạy: `cd reelo-backend && .venv/bin/python -m pytest tests/test_tracer_bullet.py -v` (test video self-skip nếu thiếu ffmpeg).
   - **Glue fix kèm theo:** `clients/stub.py::SilentVoiceClient` nay ghi MP3 **thật, giải mã được** (ffmpeg `anullsrc`→libmp3lame, độ dài theo số ký tự; fallback frame MPEG hợp lệ nếu thiếu ffmpeg) — bản cũ tạo frame ffmpeg 8.x không probe được nên không qua được concat/re-encode của renderer. `EchoScriptClient` nay sinh đúng số segment theo chunk plan (đọc khoảng index trong user message) nên lazy-script full-stub validate được.

---

## Thứ tự triển khai đề xuất
1. **Hạ tầng:** FastAPI + Google OAuth + Postgres (Alembic) + Redis/Arq + object storage adapter.
2. **Module 3:** registry + base client + KeyStore (envelope) + clients gemini(AI Studio)/eleven/kie/edge + validate-on-save.
3. **Module 1:** wizard (history-based, JSON mode) + approve + SeriesSpec + lazy script (chunked).
4. **Module 2:** materialize + Arq produce task + render.py (Ken Burns/aspect/ducking) + SRT + thumbnail + upload.
5. **Wire UI:** Google login + 9 hàm api.ts + field Setup mới; **tracer-bullet** end-to-end.
6. Hoàn thiện: retry/resume, providers còn lại, cost estimate/usage dashboard, quota.
