# Module 3 — AI Service Manager

> Quản lý **API key per-user (BYOK)** và các client gọi tới mọi AI service (Google AI Studio, ChatGPT, Claude, DeepSeek, ElevenLabs, Edge-TTS, kie.ai, HuggingFace…). Là **nền tảng** Module 1 & 2 đều dùng. Đọc trước.

Bối cảnh: **Reelo là multi-tenant SaaS** (Google OAuth, Postgres, Redis + Arq worker). Mọi thiết kế dưới đây theo lăng kính web/đa-người-dùng.

---

## 0. Quyết định đã chốt (decisions log)

| # | Quyết định | Hệ quả thiết kế |
|---|---|---|
| M3-1 | **Multi-tenant SaaS** | Key & series scope theo `user_id`; mọi client lấy key của user hiện tại |
| M3-2 | **BYOK** — user tự mang key (kể cả free tier) | Reelo không chịu chi phí AI; không key chung |
| M3-3 | **Bỏ hẳn gemini-web2api** | Provider script "free" = **Google AI Studio official API** (BYOK free tier); **không sidecar** |
| M3-4 | **Mã hóa key kiểu web** | **Envelope/AES-256-GCM, master key từ env/secret manager (KMS-ready)**, per-user |
| M3-5 | **Validate key khi lưu** | Test call nhẹ (vd list models) khi user lưu key |
| M3-6 | **Theo dõi usage + pricing** | `pricing` trong services.yaml + bảng `usage_log` per-user → cost estimate (Module 2) |
| M3-7 | **Worker queue: Arq (Redis)** | Client được gọi từ **Arq task** (worker), không chạy trong web process |
| M3-8 | **Không quota per-user ở v1** | Worker pool tự giới hạn; fair-scheduling để sau |
| M3-9 | **OmniVoice = voice clone NATIVE, Reelo-hosted GPU** | Ngoại lệ BYOK: model GPU (k2-fsa) chạy ở **microservice riêng** (`services/omnivoice/`), keyless với user, gọi qua `OMNIVOICE_URL`. Provider `omnivoice`, `cost_tier: paid` (compute Reelo trả), cần **voice sample** (upload). KHÔNG vào `routing.fallback` — chọn explicit. |

> **Hệ quả lớn cho Module 1:** mọi provider script giờ là **API chính thức** → đều hỗ trợ **JSON mode + system prompt thật**. Cơ chế "sentinel + parse fragile" của gemini-web2api **không còn là đường chính** — dùng structured output native, sentinel chỉ là fallback ([Module 1](module-1-ai-chatting.md)).

---

## 1. Mục tiêu & phạm vi

3 nhiệm vụ:

| Task (enum) | Mô tả | Provider (BYOK) |
|---|---|---|
| `WRITE_SCRIPT` | Viết/refine kịch bản | gemini (Google AI Studio), chatgpt, claude, deepseek |
| `GENERATE_VOICE` | TTS | eleven (paid), edge (free, **keyless**), **omnivoice (clone, Reelo-hosted GPU)**, hf |
| `GENERATE_IMAGE` | Tạo/tìm media (ảnh, clip video, thumbnail) | kie (free credits), aistudio/imagen, openai-image, sd, **web-commons (keyless, ẢNH THẬT)**, **web-pexels (BYOK, CLIP VIDEO thật)** |

Module 3 giải quyết:
1. **Khai báo khả năng + giá** từng service ở `services.yaml`.
2. **Lớp cha `AIClient`** để thêm service mới dễ.
3. **Quản lý key per-user (BYOK)**: mã hóa at-rest, validate khi lưu, bơm đúng lúc.
4. **Theo dõi usage/chi phí** phục vụ cost estimate.

Module 1/2 không gọi thẳng service — hỏi registry: *"client WRITE_SCRIPT cho user này với provider X"*.

---

## 2. Vị trí trong kiến trúc (multi-tenant)

```
        Module 1 / Module 2 (chạy trong Arq worker — M3-7)
                    │ resolve(task, provider, user_id)
                    ▼
        ┌───────────────────────────────────────────┐
        │  ServiceRegistry (Module 3)                 │
        │  - đọc services.yaml (capabilities+pricing) │
        │  - khởi tạo client (stateless)              │
        │  - resolve theo (task, provider)            │
        │  - KeyStore.get(user_id, key_ref)           │
        │  - UsageLogger.record(user_id, ...)         │
        └───────┬───────────────────┬─────────────────┘
                │ HTTP (BYOK key)    │ subprocess (skill scripts) / pure-python (edge-tts)
                ▼                    ▼
        Google AI Studio /      generate_image.py (kie) / generate_voice.py (eleven)
        OpenAI / Anthropic /    EdgeTTSClient (keyless)
        DeepSeek / ElevenLabs
                                     │
                       Postgres: api_keys(user_id, encrypted), usage_log(user_id)
```

**Không còn sidecar** (M3-3). Client là stateless; key lấy theo `user_id` mỗi lần gọi.

---

## 3. Base class `AIClient`

```python
# reelo-backend/clients/base.py
class Task(str, Enum):
    WRITE_SCRIPT = "write-script"
    GENERATE_VOICE = "generate-voice"
    GENERATE_IMAGE = "generate-image"

class NotSupportedError(Exception): ...
class ProviderUnavailableError(Exception): ...     # key thiếu/sai, service down, rate-limit
class InvalidKeyError(ProviderUnavailableError): ...

class AIClient(ABC):
    provider_id: str
    capabilities: set[Task]
    cost_tier: str             # "free" | "paid"  (phân loại chi phí — M3-2/M3-6)
    requires_key: bool         # True trừ Edge-TTS

    def __init__(self, config: "ServiceConfig"): ...

    def supports(self, task: Task) -> bool: ...

    async def is_available(self, ctx: "CallContext") -> bool:
        """ctx mang user_id + KeyStore. requires_key -> phải có key của user."""
        return (not self.requires_key) or ctx.keys.has(ctx.user_id, self.config.auth.key_ref)

    async def validate_key(self, ctx) -> bool:
        """Test call nhẹ (M3-5). Mặc định gọi health/list; override khi cần."""
        ...

    # capability methods — chỉ override cái nào khai báo; nhận ctx (user_id+key) + out_path khi cần
    async def write_script(self, req: "ScriptRequest", ctx) -> "ScriptResult": raise NotSupportedError
    async def generate_voice(self, req: "VoiceRequest", out_path: Path, ctx) -> "VoiceResult": raise NotSupportedError
    async def generate_image(self, req: "ImageRequest", out_path: Path, ctx) -> "ImageResult": raise NotSupportedError
```

`CallContext` = `{user_id, keys: KeyStore, usage: UsageLogger}` — truyền vào mọi call để client (a) lấy đúng key của user, (b) ghi usage.

### Request/Result DTO (cập nhật)

```python
@dataclass
class ScriptRequest:
    messages: list[dict]          # [{role, content}] — TA tự quản lý history (không conversation_id)
    system: str | None = None     # system prompt THẬT (provider chính thức hỗ trợ — M3-3)
    model: str | None = None
    json_schema: dict | None = None  # structured output native (response_format/response_schema)
    temperature: float | None = None

@dataclass
class ScriptResult:
    text: str
    model: str = ""
    usage: dict | None = None     # prompt/completion tokens -> tính chi phí
    raw: dict | None = None

# VoiceRequest: nhận text trực tiếp (cho chunk) HOẶC text_file
@dataclass
class VoiceRequest:
    voice_id: str
    text: str | None = None
    text_file: Path | None = None
    settings: dict | None = None
    # ---- voice-clone (OmniVoice) — optional, NGƯỢC TƯƠNG THÍCH ----
    ref_audio: Path | None = None    # wav mẫu (worker đã tải từ storage); chỉ omnivoice đọc
    ref_text: str | None = None      # transcript của đoạn mẫu
    language: str | None = None      # ngôn ngữ đích (600+); preset client (edge/eleven) bỏ qua

@dataclass
class ImageRequest:
    prompt_file: Path | None = None
    prompt: str | None = None     # cho thumbnail (prompt trực tiếp)
    size: str = "16:9"
    query: str | None = None      # từ khóa search ảnh THẬT (web-commons); generative bỏ qua
    label: str | None = None      # slug segment, fallback de-slug cho query
# ImageResult.raw mang attribution cho web-photo provider:
#   {attribution: {title, author, license, source_url, descriptionurl}}

# Candidate curation (M2-12/M2-13) cho web-* media provider:
@dataclass
class MediaCandidate:                # 1 ứng viên media (ẢNH hoặc CLIP) để con người chọn
    id: str                          # id ổn định (title Commons / "pexels-<id>")
    thumb_url: str                   # preview NHỎ (lưới chọn) — poster cho clip
    full_url: str                    # ảnh: raster lớn ~1600px; video: = video_url
    title: str; author: str; license: str
    source_url: str; descriptionurl: str
    width: int; height: int
    media_type: str = "image"        # "image" | "video" (default image — ngược tương thích)
    duration: float = 0.0            # độ dài clip (giây, chỉ video)
    poster_url: str = ""             # ảnh đại diện clip cho lưới (chỉ video)
    preview_url: str = ""            # clip preview ngắn hover-play (optional, video)
    video_url: str = ""              # file mp4 tải lúc render (chỉ video)
ImageCandidate = MediaCandidate      # alias ngược tương thích (web-commons + test cũ)
# AIClient base: supports_candidates = False (mặc định; AI provider auto, không chọn)
#   async search_candidates(query, ctx, *, size, limit=9, exclude) -> list[MediaCandidate]
#   async download_chosen(candidate, out_path, ctx) -> ImageResult   # tải bản đã chọn
```

### Ba họ client
| Họ | Cài đặt | Provider |
|---|---|---|
| **Native HTTP/SDK** | Gọi API trực tiếp | gemini (google-genai), chatgpt/deepseek (OpenAI SDK), claude (Anthropic SDK), edge (edge-tts), hf |
| **Skill-wrapper** | Shell ra script skill, parse stdout JSON | eleven (`generate_voice.py`), kie (`generate_image.py`) |
| **Web media (`web-*`)** | Search API + lọc license + tải media THẬT | **web-commons** ảnh (Wikimedia Commons, keyless); **web-pexels** clip video (Pexels, BYOK) |
| **Microservice (GPU)** | HTTP tới service riêng (model GPU không nạp vào worker) | **omnivoice** voice clone (k2-fsa, Reelo-hosted) |

> `write_script` luôn native. `generate_voice`/`generate_image` của eleven/kie bọc skill script. **Edge-TTS** là pure-Python (gói `edge-tts`), keyless → đường free không cần key.
>
> **Web-photo provider (`web-*`) — ẢNH THẬT (M2-11):** `clients/commons_image.py::CommonsImageClient` (`GENERATE_IMAGE`, `cost_tier=free`, `requires_key=False`, `provider_id="web-commons"`) search Wikimedia Commons (`generator=search`, namespace 6, `iiprop=url|extmetadata|mime`), **lọc license chỉ PD/CC0/CC-BY** (loại CC-BY-NC/-ND/fair-use), chỉ raster (jpeg/png), tải về `out_path`, trả attribution `{title, author, license, source_url, descriptionurl}` trong `ImageResult.raw["attribution"]`. Query lấy từ `ImageRequest.query` (= `SegmentSpec.image_query`) → de-slug `label`/stem → vài từ đầu prompt; tránh trùng ảnh trong 1 tập qua `ctx.extra["commons_used"]`. Bắt buộc `User-Agent`. Keyless ⇒ `validate_key=True`, `is_available=True`. Đây là tiền tố `web-*` cho nguồn ảnh web tương lai (openverse/pexels) — cùng họ search/license-filter/attribution. `ImageRequest` thêm optional `query`/`label`; client generative cũ bỏ qua (backward-compatible).
>
> **Candidate curation (M2-12):** web-* client đặt `supports_candidates = True` (base AIClient mặc định `False`) để Module 2 biết provider nào có bước CHỌN. Thêm 2 method: `search_candidates(query, ctx, *, size, limit=9, exclude)` → list `MediaCandidate` (search → lọc license → tối đa `limit`; mỗi candidate có preview nhỏ + attribution; **không tải file**, chỉ metadata/preview); `download_chosen(candidate, out_path, ctx)` → tải bản đã chọn về `out_path` + attribution trong `ImageResult.raw`. `generate_image` cũ giữ làm đường AUTO fallback. `MediaCandidate` (cũ `ImageCandidate`, alias giữ) định nghĩa ở `clients/base.py` (cross-module DTO).
>
> **Voice-clone provider `omnivoice` — CLONE GIỌNG (M3-9):** `clients/omnivoice.py::OmniVoiceClient` (`GENERATE_VOICE`, `cost_tier=paid` — compute GPU Reelo trả, `requires_key=False`, `provider_id="omnivoice"`). OmniVoice (k2-fsa, zero-shot, 600+ ngôn ngữ, Qwen3-0.6B) là **thư viện Python cần GPU CUDA** → chạy ở **microservice riêng** `services/omnivoice/` (FastAPI: `GET /health`, `POST /clone` multipart `ref_audio`+`ref_text`+`text`+`language?` → `audio/wav` 24kHz; nạp model lazy lúc gọi đầu; cờ `OMNIVOICE_MOCK=1` trả silence để smoke test không GPU). Client POST tới `{OMNIVOICE_URL}/clone`, nhận wav 24kHz → **transcode mp3** (`ffmpeg -i in.wav -b:a 192k`) cho đồng nhất pipeline concat/render. `is_available` = có `OMNIVOICE_URL` (config) hoặc `endpoint` trong YAML (không ping `/health` ở hot path; lỗi service lúc gọi → `ProviderUnavailableError`). `validate_key`/`health_ok` = ping `/health`. KHÁC các voice provider: KHÔNG dùng `voice_id` — mỗi call gửi `ref_audio`/`ref_text`/`language` (đọc từ `VoiceConfig.voice_sample`, worker tải mẫu từ object storage về temp). KHÔNG ở `routing.fallback` (cần sample) — chọn explicit ở Setup.
>
> **Web-video provider `web-pexels` — CLIP THẬT (M2-13):** `clients/pexels_video.py::PexelsVideoClient` (`GENERATE_IMAGE`, `cost_tier=free`, `requires_key=True`, key_ref `pexels`/env `PEXELS_API_KEY`, `provider_id="web-pexels"`) gọi Pexels Video Search (`https://api.pexels.com/videos/search`, header `Authorization: <key>`), trả `MediaCandidate(media_type="video")` với `poster_url`=video.image, `video_url`=file mp4 **gần khung nhất** (chọn theo |diện tích−khung|, ưu tiên HD/FHD), `duration`, `author`=user.name, `license="Pexels License (free, CC0-like)"`, `source_url`=trang Pexels, `preview_url`=clip nhỏ (hover-play). `search_candidates` KHÔNG tải file; `download_chosen` tải `video_url` về `.mp4`. `validate_key` = 1 call nhẹ (`query=nature, per_page=1`). Không có key Pexels → `is_available=False` ⇒ lưới curate không có clip (graceful, chỉ còn ảnh Commons). Cùng họ `web-*` với web-commons (search/license/attribution). Provider id `web` (aggregate) gộp mọi `web-*` khả dụng để trộn ảnh + clip 1 lưới (logic ở `module2/curation.py`).

---

## 4. `services.yaml` — capabilities + pricing (BYOK)

```yaml
# reelo-backend/services.yaml
services:
  gemini:                                   # M3-3: Google AI Studio OFFICIAL API (thay web2api)
    display_name: "Gemini (Google AI Studio)"
    client: "clients.gemini.GeminiClient"   # google-genai SDK / OpenAI-compat endpoint
    cost_tier: free                          # free tier, nhưng cần key của user (BYOK)
    auth: { type: key, key_ref: "google_aistudio" }
    key_help_url: "https://aistudio.google.com/apikey"
    tasks:
      write-script:
        models: ["gemini-2.x-flash", "gemini-2.x-pro"]
        default_model: "gemini-2.x-flash"
        supports_json_mode: true            # << khác hẳn web2api
        supports_system_prompt: true        # <<
      generate-image:
        models: ["imagen-..."]               # Imagen qua AI Studio
        sizes: ["16:9", "9:16", "1:1"]
    pricing: { write-script: { per_1k_input: 0.0, per_1k_output: 0.0 }, generate-image: { per_image: 0.0 } }

  chatgpt:
    display_name: "ChatGPT (OpenAI)"
    client: "clients.openai_compat.OpenAIStyleClient"
    cost_tier: paid
    auth: { type: key, key_ref: "openai" }
    tasks:
      write-script:  { default_model: "gpt-...", supports_json_mode: true, supports_system_prompt: true }
      generate-image: { default_model: "gpt-image-...", sizes: ["1024x1024","1792x1024"] }
    pricing: { write-script: { per_1k_input: 0.x, per_1k_output: 0.x }, generate-image: { per_image: 0.x } }

  claude:
    display_name: "Claude (Anthropic)"
    client: "clients.anthropic.ClaudeClient"
    cost_tier: paid
    auth: { type: key, key_ref: "anthropic" }
    tasks: { write-script: { default_model: "claude-...", supports_json_mode: true, supports_system_prompt: true } }
    pricing: { write-script: { per_1k_input: 0.x, per_1k_output: 0.x } }

  deepseek:
    display_name: "DeepSeek"
    client: "clients.openai_compat.OpenAIStyleClient"
    cost_tier: paid                          # rất rẻ
    auth: { type: key, key_ref: "deepseek" }
    endpoint: "https://api.deepseek.com/v1"
    tasks: { write-script: { default_model: "deepseek-chat", supports_json_mode: true } }

  eleven:
    display_name: "ElevenLabs"
    client: "clients.skill_voice.SkillVoiceClient"   # shell generate_voice.py
    cost_tier: paid
    auth: { type: key, key_ref: "elevenlabs", env: "ELEVENLABS_API_KEY" }
    tasks: { generate-voice: { default_voice_id: "JBFqnCBsd6RMkjVDRZzb", models: ["eleven_multilingual_v2"] } }
    pricing: { generate-voice: { per_1k_chars: 0.x } }

  edge:
    display_name: "Edge-TTS (free)"
    client: "clients.edge_tts.EdgeTTSClient"  # pure-python, KEYLESS
    cost_tier: free
    auth: { type: none }                       # << duy nhất không cần key
    tasks: { generate-voice: { voices_by_language: { vi: "vi-VN-HoaiMyNeural", en: "en-US-AndrewNeural" }, char_limit: 8000 } }

  omnivoice:                                    # voice CLONE, Reelo-hosted GPU (M3-9)
    display_name: "Giọng clone (OmniVoice)"
    client: "clients.omnivoice.OmniVoiceClient"
    cost_tier: paid                             # compute GPU Reelo trả (không BYOK)
    auth: { type: none }                        # keyless với user; gate bằng OMNIVOICE_URL
    # endpoint: "http://localhost:8002"         # optional override; else env OMNIVOICE_URL
    tasks: { generate-voice: { mode: clone, char_limit: 6000 } }   # zero-shot, cần voice_sample
    pricing: { generate-voice: { per_1k_chars: 0.0 } }   # đặt theo $/GPU-hour thực tế
  # routing.fallback.generate-voice = [eleven, edge, hf]  (omnivoice KHÔNG vào — chọn explicit)

  kie:
    display_name: "kie.ai"
    client: "clients.skill_image.SkillImageClient"   # shell generate_image.py
    cost_tier: free                            # free credits, vẫn cần key
    auth: { type: key, key_ref: "kie", env: "KIE_API_KEY" }
    tasks: { generate-image: { sizes: ["16:9","9:16","4:3","1:1"], default_size: "16:9" } }
    pricing: { generate-image: { per_image: 0.x } }

  web-commons:                                # M2-11: ẢNH THẬT từ Wikimedia Commons (keyless)
    display_name: "Web · Ảnh thật (Commons)"
    client: "clients.commons_image.CommonsImageClient"
    cost_tier: free
    auth: { type: none }                       # keyless — không ô key ở Setup
    key_help_url: null
    tasks: { generate-image: { sizes: ["16:9","9:16","1:1","4:3","3:4"], default_size: "16:9",
                               license_filter: ["public-domain","cc0","cc-by"] } }
    pricing: { generate-image: { per_image: 0.0 } }

  web-pexels:                                 # M2-13: CLIP VIDEO THẬT từ Pexels (BYOK, CC0-like)
    display_name: "Web · Video clip (Pexels)"
    client: "clients.pexels_video.PexelsVideoClient"
    cost_tier: free                            # API free, vẫn cần key Pexels
    auth: { type: key, key_ref: "pexels", env: "PEXELS_API_KEY" }
    key_help_url: "https://www.pexels.com/api/"
    tasks: { generate-image: { sizes: ["16:9","9:16","1:1","4:3","3:4"], default_size: "16:9",
                               search_limit: 15 } }   # media_type="video"
    pricing: { generate-image: { per_image: 0.0 } }

  # hf / sd: scaffold

routing:
  fallback:                                    # BYOK-aware: chỉ rơi sang provider user CÓ key
    write-script:   ["claude", "chatgpt", "gemini", "deepseek"]
    generate-voice: ["eleven", "edge", "hf"]
    generate-image: ["kie", "gemini", "chatgpt", "web-commons", "web-pexels", "sd"]   # web-commons keyless; web-pexels BYOK
```

**Đồng bộ UI:** `GET /providers` derive từ YAML. Lưu ý reelo-ui cần đổi: vì **BYOK**, hầu hết provider đều `auth.type=key` → hiển thị ô nhập key cho cả provider `cost_tier: free` (trừ `edge` keyless). `tier` cũ (free/key) của [data.ts](../reelo-ui/lib/data.ts) nên tách thành **`cost_tier` (free/paid, để gắn nhãn)** + **`requires_key`** (có/không ô key) + `key_help_url` (link lấy key free).

---

## 5. `ServiceRegistry` / factory (per-user)

```python
class ServiceRegistry:
    def __init__(self, yaml_path):
        cfg = load_yaml(yaml_path); self._fallback = cfg["routing"]["fallback"]
        self._clients = {pid: import_dotted(sc["client"])(ServiceConfig(pid, sc))
                         for pid, sc in cfg["services"].items()}

    def get(self, provider_id) -> AIClient: ...

    async def resolve(self, task: Task, preferred: str, ctx: CallContext) -> AIClient:
        """Thử provider user chọn; nếu không khả dụng -> fallback CHỈ trong số provider user CÓ key."""
        chain = [preferred] + [p for p in self._fallback[task.value] if p != preferred]
        for pid in chain:
            c = self._clients.get(pid)
            if c and c.supports(task) and await c.is_available(ctx):
                return c
        raise ProviderUnavailableError(f"User chưa cấu hình provider khả dụng cho {task}")

    def for_capability(self, task) -> list[AIClient]: ...
```

- BYOK fallback: nếu user chỉ có key của provider họ chọn, fallback chain bỏ qua provider không có key → báo lỗi rõ "hãy nhập key cho X" thay vì tự nhảy lung tung.

---

## 6. Thêm service mới (vẫn dễ)
1. Viết 1 class kế thừa `AIClient`, implement method có khả năng. *OpenAI-compatible → tái dùng `OpenAIStyleClient`, 0 code.*
2. Thêm khối `services:` (client/cost_tier/auth/tasks/models/pricing).
3. Thêm vào `routing.fallback`.

Không sửa registry/factory.

---

## 7. KeyStore — BYOK + mã hóa kiểu web (M3-1, M3-4, M3-5)

```python
# reelo-backend/keystore.py
class KeyStore:
    """Key per-user, mã hóa at-rest bằng AES-256-GCM với master key từ env/KMS."""
    def __init__(self, master_key: bytes): ...           # REELO_MASTER_KEY (env) hoặc KMS-wrapped DEK

    def has(self, user_id: str, key_ref: str) -> bool: ...
    def get(self, user_id: str, key_ref: str) -> str | None: ...   # giải mã khi đọc
    def save(self, user_id: str, key_ref: str, value: str): ...    # mã hóa khi ghi -> Postgres
    def as_env(self, user_id, mapping) -> dict[str, str]: ...       # bơm cho subprocess skill
```

**Mã hóa (cách các dự án web lớn làm — M3-4):**
- **Master key (KEK)** từ env `REELO_MASTER_KEY` (v1) → nâng lên **cloud KMS** (AWS/GCP KMS) cho production.
- Mỗi key user: AES-256-GCM với **nonce ngẫu nhiên/record**; lưu `ciphertext + nonce + tag` vào bảng `api_keys(user_id, key_ref, ciphertext, nonce, ...)`.
- Nâng cấp envelope thật: per-user **DEK** sinh ngẫu nhiên, mã hóa data bằng DEK, DEK được KEK/KMS wrap. (Roadmap.)
- **Không bao giờ** log/trả plaintext; `/keys/status` chỉ trả presence.

**Validate khi lưu (M3-5):** `save()` → gọi `client.validate_key(ctx)` (test call rẻ, vd `GET /models` hoặc 1-token completion). Sai → trả lỗi cho UI ngay, không lưu (hoặc lưu kèm cờ invalid).

**Bơm cho skill script:** worker gọi `keys.as_env(user_id, {"ELEVENLABS_API_KEY": "elevenlabs"})` → đưa vào **env subprocess** đúng run. Key không vào spec/project/log. (`load_dotenv` của skill không override env đã set — verify; nếu override thì ghi `.env` riêng theo job — Risk #2 [integration.md](integration.md).)

---

## 8. Tích hợp worker (Arq — M3-7)

- Client **stateless**, được gọi **bên trong Arq task** (worker process), không trong web request.
- Web (FastAPI) chỉ **enqueue** job (vd `arq.enqueue_job("produce_episode", user_id, episode_id)`) rồi trả `jobId`; worker chạy Module 1 (lazy script) / Module 2 (assets+render) và gọi Module 3 clients.
- `CallContext` (user_id + KeyStore + UsageLogger) được dựng trong worker từ `user_id` truyền qua job payload.
- Concurrency: số worker + concurrency mỗi worker là giới hạn tự nhiên (không quota per-user ở v1 — M3-8).

---

## 9. Usage & pricing (M3-6)

- Mỗi call thành công → `UsageLogger.record(user_id, provider, task, units, cost)`:
  - script: tokens (từ `ScriptResult.usage`) × `pricing.per_1k_*`.
  - voice: số ký tự × `per_1k_chars`.
  - image: số ảnh × `per_image`.
- Bảng `usage_log(user_id, provider, task, units, cost, ts)` (Postgres).
- **Cost estimate trước produce** (Module 2 §9) = dự tính units × pricing (chưa gọi). Vì BYOK, đây là tiền của **user**, hiển thị để họ chủ động.
- Phục vụ dashboard usage/lịch sử sau này.

---

## 10. Auth boundary (OAuth — multi-tenant)

- **Google OAuth** ở tầng app (mô tả ở [integration.md](integration.md)); Module 3 chỉ nhận `user_id` đã xác thực và scope key/usage theo đó.
- Provider key (BYOK) **khác** OAuth login — login là Google account của user; key là token các AI service user tự lấy.
- *Lưu ý tương lai:* nếu thêm auto-upload YouTube, OAuth Google có thể tái dùng scope YouTube — nhưng đó là roadmap (risk #6 [integration.md](integration.md)).

---

## 11. Xử lý lỗi & fallback (BYOK-aware)
- `InvalidKeyError` (401/403) → báo user cập nhật key provider đó; không tự dùng key khác.
- `ProviderUnavailableError` (5xx/timeout/rate-limit) → thử fallback **trong số provider user có key**; hết → lỗi rõ ràng.
- Phân biệt với *parse failure* của Module 1 (lỗi format, retry cùng provider) — xem [Module 1 §9](module-1-ai-chatting.md).
- Retry/backoff: skill script đã retry 3× nội bộ; client native nên có timeout + 1-2 retry cho 5xx.

---

## 12. Endpoint Module 3
| Method | Path | Map UI | Mô tả |
|---|---|---|---|
| `GET` | `/providers` | (init Setup) | Derive từ YAML: theo task + `cost_tier` + `requires_key` + `key_help_url` |
| `POST` | `/keys` | `saveApiKey(provider, key)` | **Validate (test call) rồi** mã hóa lưu per-user |
| `GET` | `/keys/status` | (badge "Đã lưu") | `{key_ref: {present, valid}}` — **không trả giá trị** |
| `DELETE` | `/keys/{key_ref}` | (xoá key) | Xoá key của user |
| `GET` | `/usage` | (dashboard, sau) | Tổng usage/chi phí per-user |

---

## 13. Open questions còn lại
1. **Map `provider` (UI id) ↔ `key_ref` (YAML).** Lấy từ `auth.key_ref`; đảm bảo `saveApiKey("eleven",...)` ghi đúng `elevenlabs`.
2. **reelo-ui đổi PROVIDERS:** tách `tier`→`cost_tier`+`requires_key`+`key_help_url`; hiện ô key cho cả provider free (trừ edge). Đây là sửa frontend.
3. **gemini official client:** dùng `google-genai` SDK hay OpenAI-compat endpoint của Google? (Cả hai hỗ trợ JSON mode + system instruction; chốt khi code.)
4. **Master key rotation:** quy trình xoay `REELO_MASTER_KEY` / nâng lên KMS — thiết kế re-encrypt khi cần.
5. **Pricing numbers:** điền `pricing` thực cho từng provider (đơn giá hiện hành).
6. **Edge-TTS keyless ở multi-tenant:** không có key nhưng dùng chung IP server → có thể bị Microsoft rate-limit. Theo dõi; cần thì proxy/limit.

---

## Liên kết
- `write_script` + JSON mode/system prompt native + parse fallback: [module-1-ai-chatting.md](module-1-ai-chatting.md)
- `generate_voice`/`generate_image` được worker orchestrate: [module-2-video-generator.md](module-2-video-generator.md)
- Topology web+Arq+Postgres+Redis+OAuth, persistence, BYOK, risks: [integration.md](integration.md)
