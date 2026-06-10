# OmniVoice microservice (Reelo voice-clone provider)

Thin FastAPI wrapper over [k2-fsa/OmniVoice](https://huggingface.co/k2-fsa/OmniVoice),
a zero-shot voice-cloning TTS (600+ languages, Qwen3-0.6B). OmniVoice ships only
as a Python library, so Reelo runs it as a **separate microservice on a GPU host**
and reaches it over HTTP via `OMNIVOICE_URL`. The model never loads into the Arq
worker.

## Requirements

- **A CUDA GPU host.** The model needs PyTorch + an NVIDIA GPU. It will not run
  on macOS / CPU in any usable time. The Reelo backend (this repo) does **not**
  depend on torch — only this service does.
- Python 3.10+.

## Install (GPU host)

```bash
cd reelo-backend/services/omnivoice
# If you need a specific CUDA build of torch, install it first, e.g.:
#   pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121
pip install -r requirements.txt
```

## Run

```bash
uvicorn server:app --host 0.0.0.0 --port 8002
```

The model is loaded lazily on the first `POST /clone` (so startup is instant and
a missing GPU surfaces as a clean `503`, not a crash).

Then point Reelo at it:

```bash
# in reelo-backend/.env
OMNIVOICE_URL=http://<gpu-host>:8002
```

## Smoke test without a GPU (mock mode)

`OMNIVOICE_MOCK=1` makes the server return 24 kHz **silence** instead of loading
the model — `torch`/`omnivoice` are never imported, so it runs anywhere (CI,
macOS). Use it to exercise the full wire path (upload sample → produce → mp3):

```bash
OMNIVOICE_MOCK=1 uvicorn server:app --port 8002
```

## API

### `GET /health`

```json
{ "status": "ok", "model": "k2-fsa/OmniVoice", "device": "cuda:0",
  "sample_rate": 24000, "mock": false, "loaded": true }
```

### `POST /clone`  (multipart/form-data)

| field       | type          | required | description                              |
|-------------|---------------|----------|------------------------------------------|
| `ref_audio` | file (wav)    | yes      | reference voice sample (mono wav best)   |
| `ref_text`  | string        | yes      | exact transcript of the reference audio  |
| `text`      | string        | yes      | target text to speak in the cloned voice |
| `language`  | string        | no       | language code/name (e.g. `vi`, `en`)     |

Response: `audio/wav` bytes, mono, **24 kHz** (`SAMPLE_RATE`).

Errors: `400` (empty `text`/`ref_text`/`ref_audio`), `500` (generation failure),
`503` (runtime/model unavailable — no GPU/weights). Reelo maps non-2xx to
`ProviderUnavailableError`.

## Environment

| var                     | default          | meaning                                  |
|-------------------------|------------------|------------------------------------------|
| `OMNIVOICE_MOCK`        | (unset)          | `1` → serve silence, never load the model |
| `OMNIVOICE_MODEL`       | `k2-fsa/OmniVoice` | HF model id                            |
| `OMNIVOICE_DEVICE`      | `cuda:0`         | torch device_map                         |
| `OMNIVOICE_SAMPLE_RATE` | `24000`          | output WAV sample rate                   |

## Notes / limits

- This is a **reference** wrapper, not a hardened production server: no auth,
  single-process, one model instance. Front it with whatever ingress/auth your
  GPU host uses, or restrict to Reelo's private network.
- Reelo (not the user) hosts this GPU service, so voice-clone is a **Reelo-hosted
  paid** capability (no per-user BYOK key) — see `cost_tier: paid` in
  `services.yaml`.
