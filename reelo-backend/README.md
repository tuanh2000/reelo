# Reelo Backend

Multi-tenant SaaS backend for Reelo — turn an idea into a YouTube video with AI.
FastAPI (web) + Arq worker (Redis) + Postgres + object storage, **BYOK** (every
provider uses the user's own key), Google OAuth.

All three modules are implemented on top of the shared contracts + infra:

- **Module 1 — reelo-scriptwriting**: wizard chat, `SeriesSpec`, lazy episode script.
- **Module 2 — reelo-video-generator**: materialize, `produce_episode`, render, SRT, thumbnail.
- **Module 3 — reelo-ai-services**: `AIClient` clients, `services.yaml`, `KeyStore`, registry, usage.

REST endpoints exist for all 9 `reelo-ui/lib/api.ts` functions (+ Module 1/2/3
extras). The app boots cleanly without a live DB/Redis. A full **tracer-bullet**
(`tests/test_tracer_bullet.py`) proves the pipeline assembles a real `final.mp4`
using keyless stub providers — no API keys or Google login required.

See `../docs/` for the full design (integration, module-1/2/3).

## Layout

```
reelo-backend/
├── config.py                # pydantic-settings (env)
├── keystore.py              # AES-256-GCM BYOK key store (Cipher + KeyStore)
├── usage.py                 # UsageLogger + cost math
├── services.yaml            # provider catalog (Module 3 fills in)
├── models/                  # shared Pydantic contracts (spec.py, jobs.py)
├── clients/                 # base.py: AIClient ABC + DTOs + CallContext (Module 3 adds clients)
├── db/                      # async engine/session, ORM models, repositories, alembic migrations
├── storage/                 # object storage adapter (local + s3)
├── worker/                  # Arq WorkerSettings, task skeletons, enqueue helper
├── web/                     # FastAPI app, auth (Google OAuth), deps, schemas, routers
├── skills/                  # skills/<id>/template.yaml (Module 1 content)
├── styles/presets.yaml      # image-style presets (Module 1 content)
├── services/omnivoice/      # OmniVoice voice-clone microservice (GPU host; deployed separately)
└── tests/                   # smoke tests (no DB/Redis needed)
```

### Voice clone (OmniVoice)

`omnivoice` is a NATIVE voice provider that clones a user-uploaded sample
(k2-fsa OmniVoice, zero-shot, 600+ languages). The model is GPU-only and ships as
a Python library, so it runs as a **separate microservice** on a CUDA host —
`services/omnivoice/` (see its README) — and Reelo reaches it via `OMNIVOICE_URL`.
The backend itself does not depend on torch. Flow: user uploads sample +
transcript + language (`POST /series/{id}/voice-sample`) → on produce, the voice
stage clones each chunk via `{OMNIVOICE_URL}/clone` (wav 24 kHz → mp3). For a
no-GPU smoke test, run the service with `OMNIVOICE_MOCK=1` (returns silence).

## Prerequisites

- Python **3.11+**
- Postgres + Redis (via `docker-compose.dev.yml`)
- `ffmpeg` on PATH (only needed once Module 2 renders)

## Setup

```bash
cd reelo-backend

# 1) Create a virtualenv and install (uv recommended; plain venv works too)
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
# or:  python3.11 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"

# 2) Configure env
cp .env.example .env
#   - REELO_MASTER_KEY: python -c "import os,base64;print(base64.b64encode(os.urandom(32)).decode())"
#   - SESSION_SECRET:   python -c "import secrets;print(secrets.token_urlsafe(48))"
#   - GOOGLE_OAUTH_CLIENT_ID / SECRET from Google Cloud console

# 3) Start infra
docker compose -f docker-compose.dev.yml up -d

# 4) Run migrations
alembic upgrade head
```

## Run

```bash
# Web API (http://localhost:8000, docs at /docs)
uvicorn web.app:app --reload

# Arq worker (separate terminal)
arq worker.settings.WorkerSettings
```

Health check: `curl http://localhost:8000/health`.
Login flow: open `http://localhost:8000/auth/login` (needs Google OAuth configured).

## Test

```bash
pytest                                  # full suite (no DB/Redis needed)
pytest tests/test_tracer_bullet.py -v   # end-to-end pipeline (real ffmpeg render)
pytest tests/test_image_candidates.py   # web-photo human curation (M2-12), offline
pytest tests/test_web_pexels_video.py   # web-pexels video clips (M2-13), offline
pytest tests/test_media_curation_merge.py  # mixed photo+clip grid + runner (M2-13)
pytest "tests/test_module2_render.py::test_smoke_render_mixed_image_and_video"  # real
                                        # mixed image+clip render -> h264+aac mp4

# Live web-photo verification (network + ffmpeg; self-skips offline):
python verify_web_images.py             # auto-pick real photos -> final.mp4
python verify_web_images.py --curated   # human-curation path (M2-12): search
                                        # candidates, pick one/segment, render
```

The suite covers app boot + health, auth enforcement, Pydantic contracts,
AES-256-GCM round-trips, all three module pipelines, and the **tracer-bullet**.
It runs **without** a database or Redis (in-memory fakes + the keyless
`stub-script` / `stub-voice` / `stub-image` providers).

The tracer-bullet's video test needs `ffmpeg`/`ffprobe` on PATH; it self-skips
otherwise. It drives `build_series_spec → generate_episode_script →
run_produce_episode` and asserts a real h264+aac `final.mp4` (ffprobe-readable),
a `subs.srt`, and 3 thumbnails, with the episode flipped to `assembled`.

> **Stub voice MP3**: `SilentVoiceClient` writes a real, decodable silent MP3
> (ffmpeg `anullsrc` → libmp3lame, length scaled to the text; falls back to a
> repeated valid MPEG frame if ffmpeg is missing). This is what lets the
> full-stub pipeline survive the renderer's concat/re-encode under ffmpeg 8.x.

## Run the whole system end-to-end (real keys)

```bash
# 0) infra
docker compose -f docker-compose.dev.yml up -d && alembic upgrade head
# 1) backend web + worker (two terminals)
uvicorn web.app:app --reload
arq worker.settings.WorkerSettings
# 2) frontend (reelo-ui/)
cd ../reelo-ui && NEXT_PUBLIC_API_BASE=http://localhost:8000 npm run dev
# 3) browser → http://localhost:3000 → "Đăng nhập với Google" → Setup: pick
#    providers + paste BYOK keys → wizard → approve → open episode (lazy script)
#    → generate → Review → Export (signed final.mp4 / subs.srt / thumbnail URLs).
```

Frontend env: `NEXT_PUBLIC_API_BASE` (backend base URL, default
`http://localhost:8000`) and optional `NEXT_PUBLIC_REQUIRE_AUTH=false` to skip
the login gate when demoing on mock data without a backend.

## Migrations

```bash
alembic upgrade head                 # apply
alembic revision --autogenerate -m "msg"   # new migration (needs live DB)
alembic downgrade -1                 # rollback one
```

## Notes for module owners

- Import shared contracts from `models` (`SeriesSpec`, `EpisodeSpec`,
  `SegmentSpec`, `ImageStyle`, `VoiceConfig`, `GenJob`).
- Import the AI contract from `clients.base` (`AIClient`, `Task`, request/result
  DTOs, `CallContext`, `ServiceConfig`, errors).
- Get a DB session with `db.session.get_session` (FastAPI dep) or
  `db.session.session_scope()` (worker). Use repositories in `db.repository`.
- Enqueue jobs with `worker.enqueue.enqueue_job("produce_episode", user_id, episode_id)`.
- Add routes by editing the relevant `web/routers/*.py` (already registered).
- **Never** log/return plaintext keys; `KeyStore` handles encryption.

Any change to the contracts listed above goes through the **platform-lead**
(see `../docs/agent-team.md`).
