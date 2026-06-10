# Module 1 — AI Chatting / Viết kịch bản

> Nhận ý tưởng → thảo luận qua lại → AI đề xuất outline series → user **approve** (chốt cứng) → lưu series. Full kịch bản (segments) của từng tập được sinh **lazy** khi user mở/sản xuất tập đó, qua cơ chế **RULE + parse + validate** thành `EpisodeSpec`.

Phụ thuộc: [Module 3](module-3-ai-service-manager.md) (client `WRITE_SCRIPT`). Sản phẩm: `SeriesSpec` (outline + config, lưu khi approve) và `EpisodeSpec.segments` (sinh lazy). Xem [integration.md](integration.md) cho persistence & template.

---

## 0. Quyết định đã chốt (decisions log)

| # | Quyết định | Hệ quả thiết kế |
|---|---|---|
| D1 | **Ngôn ngữ thoại chọn theo từng series** (`language`); prompt ảnh **luôn tiếng Anh** | `SeriesSpec.language`; prompt viết script theo `language`; image_prompt giữ EN |
| D2 | **Lazy generation**: approve chỉ chốt outline + config; full script sinh khi sản xuất từng tập | RULE+parse+validate **dời sang per-episode lúc produce**, không phải lúc approve |
| D3 | **Nâng cap 8 ảnh**: hỗ trợ video dài, nhiều ảnh | Phải **chunk** khi sinh script (JSON dài); Module 2 phải **sửa pipeline bỏ cap 8** |
| D4 | **Image style = preset (base_prompt visual) + skill template (cấu trúc + tradition layer), kết hợp** | `image_style` trực giao với `skill`; ghép base_prompt(preset) + style_layer(skill) |
| D5 | **Độ dài + mật độ ảnh do user nhập** (Light/Standard/Dense) | Suy `segment_count` từ `target_minutes × density`; word-budget per segment |
| D6 | **Outline user sửa tay = nguồn sự thật**; approve không gọi AI đồng bộ lại | Approve nhận outline đã sửa, persist trực tiếp |
| D7 | **Metadata YouTube sinh per-episode cùng lúc với full script** | `EpisodeSpec.youtube` sinh trong cùng lượt lazy generation |
| D8 | **Tỉ lệ khung chọn per series**: 16:9 hoặc 9:16 | `image_style.aspect`; ảnh hưởng `--size` ảnh + tham số render Module 2 |
| D9 | **Chỉ lưu khi approve** (không draft series) | Wizard **stateless** ở backend; chat sống ở `messages[]` (UI gửi) tới khi approve |
| D10 | **Approve là chốt cứng**; muốn đổi → tạo series mới | Không cần versioning spec; đơn giản hoá persistence |
| D11 | **Input = 1 ô ý tưởng tự do**; AI hỏi thêm trong chat | Không form phức tạp; prompt Pha A có khả năng "hỏi lại" |
| D12 | **Config series-level đặt ở màn Setup** (cùng skill/provider) | reelo-ui **cần thêm field** ở Setup: language, target_minutes, density, aspect |

---

## 1. Mục tiêu & nguyên tắc

Biến một câu ý tưởng mơ hồ thành:
1. **Outline series đã duyệt** (danh sách tập + config) — lưu khi approve.
2. **Kịch bản chi tiết từng tập** (`segments[]` + metadata) — sinh lazy, đủ cho Module 2 dựng video.

> **Cập nhật theo SaaS pivot ([Module 3](module-3-ai-service-manager.md)):** đã **bỏ gemini-web2api**. Mọi provider script giờ là **API chính thức (BYOK)** — Google AI Studio, OpenAI, Anthropic, DeepSeek — **đều hỗ trợ JSON mode (structured output) + system prompt thật**. Vì vậy: (a) RULE đặt vào **system prompt**, không phải nối đuôi user prompt; (b) **structured output native (`json_schema`) là đường chính**, sentinel + parse robust chỉ còn là **fallback**; (c) chat history do **ta tự quản lý (`messages[]`)**, không dùng `conversation_id`; (d) lazy script gen chạy trong **Arq worker**, scope `user_id`. Phần còn lại (lazy, density, chunk, schema) giữ nguyên.

---

## 2. Luồng tổng (cập nhật theo D2, D9, D10, D12)

```
                 ┌─────────────── PHA A: REFINE (lỏng, stateless) ──────────────┐
 1 ô ý tưởng ───►│ chat ↔ AI; AI đề xuất outline; AI hỏi thêm nếu thiếu (D11)   │
                 │ user sửa outline thủ công (rename/add/del/reorder) trên UI    │
                 │ POST /wizard/message {idea, history[]}  (backend ghép messages[])│
                 └───────────────────────────────────────────────────────────────┘
                                    │ user vào màn SETUP (D12)
                                    ▼ chọn skill, providers, language, target_minutes, density, aspect
                                    │ + chọn image style preset (D4) / inferStyle
                                    │ user bấm APPROVE (D6, D10)
                 ┌──────────────── PHA B: APPROVE (chốt cứng) ──────────────────┐
                 │ build SeriesSpec shell từ outline ĐÃ SỬA + config             │
                 │ episodes: {title, order, desc} ; segments = []  (chưa sinh)   │
                 │ status mỗi tập = "draft"                                       │
                 │ LƯU series vào DB (lần đầu chạm DB - D9)                       │
                 └───────────────────────────────────────────────────────────────┘
                                    │ user mở 1 tập trong workspace (hoặc bấm sản xuất)
                 ┌────────── LAZY SCRIPT GEN per-episode (D2, D5, D7) ───────────┐
                 │ POST /episodes/{id}/script                                     │
                 │ tính segment_count = f(target_minutes, density)               │
                 │ sinh segments (chunked) qua RULE + parse + validate            │
                 │ + sinh youtube metadata (D7)                                   │
                 │ status: draft -> "scripted"                                    │
                 └───────────────────────────────────────────────────────────────┘
                                    │ -> Module 2 produce (assets -> assembled)
```

**Điểm mấu chốt (khác bản trước):** cơ chế RULE+parse+validate **không chạy lúc approve** mà chạy **per-episode lúc sinh script lazy**. Approve chỉ persist outline + config (D6 → không cần gọi AI, chỉ lấy outline UI đã sửa).

---

## 3. Wizard session & state (D9 — không draft)

Vì **chỉ lưu khi approve** (D9), wizard **stateless** ở backend:

- **History do ta quản lý:** `sendWizardMessage(topic, history)` đã mang `history` (UI giữ). Backend ghép thành `messages[]` chuẩn (`[{role:"user"/"assistant", content}]`) gửi provider chính thức — **không cần `conversation_id`** (đã bỏ gemini-web2api). Mọi provider hỗ trợ multi-turn qua messages array.
- **Outline** sống trong **UI state** (user sửa tay liên tục). Backend không giữ outline trong lúc chat.
- **Khi user đóng giữa chừng:** mất chat (chấp nhận theo D9). Không có draft để dọn.
- **Approve** là lần đầu dữ liệu chạm DB (Postgres, scope `user_id`): tạo `series` từ outline đã sửa + config.

> Không cần bảng `wizard_session`. Trạng thái phiên = `messages[]` (UI gửi mỗi turn) + outline/config (UI). Backend chỉ stateless-process tới khi approve. Mỗi request gắn `user_id` đã đăng nhập (OAuth).

---

## 4. Pha A — Refine chat (`sendWizardMessage`)

Map: `sendWizardMessage(topic, history) -> {reply, outline?}` ([reelo-ui/lib/api.ts](../reelo-ui/lib/api.ts), [wizard.tsx](../reelo-ui/screens/wizard.tsx)).

**Hành vi:**
- User gõ **1 ý tưởng tự do** (D11). AI đề xuất outline (danh sách tập). Nếu thiếu thông tin để dựng series tốt (đối tượng khán giả, góc độ, độ sâu…), **AI chủ động hỏi lại trong hội thoại** — prompt Pha A khuyến khích điều này.
- User tinh chỉnh bằng chat ("thêm 1 tập về X", "học thuật hơn", "gộp 2 tập cuối") **và/hoặc** sửa trực tiếp outline trên UI (rename/add/delete/reorder/toggle pick).
- History gửi dạng `messages[]` mỗi turn (không `conversation_id`); chỉ dẫn (skill, ngôn ngữ, yêu cầu chủ động hỏi lại) đặt ở **system prompt**.

**Outline preview (`OutlineItem[]`):** AI trả prose tự nhiên **kèm** block nhẹ ở cuối để UI parse. **Parse fail KHÔNG fatal** — không thấy block thì chỉ hiện `reply`, giữ outline cũ. Chat phải mượt, không vỡ vì format.

```
<<<OUTLINE>>>
1 | Tiêu đề tập 1 | mô tả ngắn
2 | Tiêu đề tập 2 | mô tả ngắn
<<<END_OUTLINE>>>
```
→ map `OutlineItem{id, title, desc, pick:true}`.

**Prompt Pha A — đặt ở `system` (không nối user prompt):**
```
[system]
Bạn là trợ lý xây dựng series video [skill.display_name] bằng [language].
Người dùng đưa ý tưởng. Hãy đề xuất outline gồm các tập (title + mô tả ngắn).
Nếu ý tưởng thiếu thông tin quan trọng (đối tượng, góc nhìn, độ dài mong muốn),
hãy HỎI LẠI ngắn gọn trước khi chốt. Sau phần trả lời, xuất block <<<OUTLINE>>>...
[skill.script.rule_prompt_extra — định hướng riêng theo skill]
[messages]: history hội thoại (UI gửi) + turn mới của user
```
> Lưu ý: lúc Pha A có thể **chưa** biết `skill`/`language` nếu user chưa qua Setup. Xử lý: Pha A chạy với default (skill suy từ topic, language mặc định theo locale UI), Setup cho phép chỉnh lại trước approve. (Open Q #1.)

---

## 5. Config series-level (màn Setup — D12)

reelo-ui màn [setup.tsx](../reelo-ui/screens/setup.tsx) **cần thêm field** (ngoài skill + providers + keys hiện có):

| Field | Kiểu | Mặc định | Ghi chú |
|---|---|---|---|
| `language` | enum/locale | theo locale UI | D1 — ngôn ngữ thoại; image prompt luôn EN |
| `target_minutes` | number | 10 | D5 — thời lượng mục tiêu mỗi tập |
| `density` | enum: light/standard/dense | standard | D5 — mật độ ảnh |
| `aspect` | enum: "16:9"/"9:16" | "16:9" | D8 — tỉ lệ khung |
| `music` (upload) | file → path | none | M2-1 — nhạc nền per-series, **optional**; upload qua `POST /series/{id}/music` |
| (skill, providers) | (đã có) | | |

### Bảng mật độ ảnh & công thức suy `segment_count` (D5)

Đề xuất (configurable, có thể chỉnh khi triển khai):

| Tier | Giây/ảnh | Ví dụ video 10' | Ví dụ video 25' |
|---|---|---|---|
| **Light** | ~60s | ~10 ảnh | ~25 ảnh |
| **Standard** | ~35s | ~17 ảnh | ~43 ảnh |
| **Dense** | ~22s | ~27 ảnh | ~68 ảnh |

```
segment_count = round(target_minutes * 60 / seconds_per_image[density])
clamp vào [3, MAX_SEGMENTS]   # MAX_SEGMENTS cấu hình, vd 200 (đã bỏ cap 8 — D3)
```

**Word budget** (để AI viết đủ thời lượng):
```
wpm = 150 (EN) | 140 (VI)          # tốc độ đọc, cấu hình theo language
total_words = round(target_minutes * wpm)
words_per_segment ≈ total_words / segment_count
```
> Mỗi segment = 1 block thoại (`narration`) + 1 ảnh. Timing ảnh do skill tự tính từ word-count (xem [Module 2](module-2-video-generator.md)) nên `words_per_segment` chỉ là gợi ý cho AI; phân bổ thực tế theo `skill.script.word_ratios`.

---

## 6. Pha B — Approve (chốt cứng, D6/D10)

`POST /wizard/approve`:
```jsonc
// request
{
  "outline": [ {title, desc, pick} ],  // outline ĐÃ SỬA trên UI = nguồn sự thật (D6)
  "config": { skill, language, target_minutes, density, aspect,
              providers:{script,image,voice}, voice:{voice_id,settings},
              image_style:{preset_id, palette, description} }
}
```

Backend (KHÔNG gọi AI — D6):
1. Lọc outline `pick == true`.
2. Resolve `image_style` đầy đủ = ghép `preset.base_prompt` + `skill.template.image.style_layer?` (D4, mục 9).
3. Build `SeriesSpec` **shell**: mỗi episode = `{episode_id, title, order, desc, target_minutes, segments: [], youtube: null}`, `status="draft"`.
4. **Lưu series vào DB** (lần đầu chạm DB — D9). Trả `{series}`.

> Approve nhanh, rẻ, không tốn lượt AI. Toàn bộ chi phí AI dồn vào lazy generation per-episode.

---

## 7. Lazy script generation per-episode (D2/D5/D7) — nơi RULE+parse+validate chạy

**Trigger:** user mở tập trong workspace lần đầu (để xem/sửa script) **hoặc** bấm sản xuất. Endpoint `POST /episodes/{id}/script`. Nếu `segments` đã có → trả luôn (idempotent).

**Ranh giới Module 1 ↔ Module 2:** việc sinh script là **của Module 1** (`generate_episode_script`), nhưng có thể được Module 2's `startGeneration` gọi như "bước 0" nếu tập chưa `scripted`. Giữ logic viết-kịch-bản trong Module 1.

```python
# reelo-backend/module1/episode_script.py
async def generate_episode_script(series: SeriesSpec, ep: EpisodeSpec) -> EpisodeSpec:
    n = derive_segment_count(series.target_minutes, series.density)      # D5
    budget = derive_word_budget(series.target_minutes, series.language, n)
    tmpl = load_skill_template(series.skill)
    client = await registry.resolve(Task.WRITE_SCRIPT, series.providers["script"])

    # chunk để né truncate (D3): chia n segment theo cấu trúc skill
    chunks = plan_chunks(n, tmpl.script.structure, tmpl.script.word_ratios)  # mục 8
    segments = []
    for ch in chunks:
        spec_chunk = await run_rule_loop(client, build_prompt(series, ep, ch, tmpl, budget))
        segments.extend(spec_chunk.segments)
    reindex(segments)                                # đảm bảo index liên tục 1..n
    youtube = await generate_youtube_meta(client, series, ep, segments)     # D7
    return ep.copy(update={"segments": segments, "youtube": youtube, "status": "scripted"})
```

### 8. Chunking (D3 — video dài, JSON dài)

Sinh tất cả `n` segment trong 1 lượt với gemini (output ~20k char) sẽ **truncate** khi `n` lớn. Giải pháp: chia theo **cấu trúc skill** + giới hạn segment/chunk.

```
plan_chunks(n, structure, word_ratios):
  # phân bổ n segment cho từng phần theo word_ratios (hook/context/layers/closing)
  # rồi cắt mỗi phần thành các chunk <= SEGMENTS_PER_CHUNK (vd 8)
  -> [ {section:"hook", count:1, idx_start:1},
       {section:"layers", count:8, idx_start:2}, ... ]
```
- Mỗi chunk là **1 lượt RULE+parse+validate độc lập**, prompt nêu rõ: "viết segment [idx_start..idx_end] cho phần [section], tiếp nối mạch, ngôn ngữ [language]".
- `SEGMENTS_PER_CHUNK` cấu hình (vd 8) — đủ nhỏ để JSON không cụt với gemini.
- Provider có JSON mode + output dài (Claude) có thể đặt `SEGMENTS_PER_CHUNK` lớn hơn hoặc 1 lượt.
- **Mạch liên tục giữa chunk:** truyền tóm tắt cuối chunk trước (1-2 câu) vào `messages[]` của chunk sau.

### 9. RULE + parse + validate (chi tiết)

**Đường chính — structured output native.** Đặt schema vào `ScriptRequest.json_schema`; chỉ dẫn + `skill.script.rule_prompt_extra` đặt ở **system prompt**. Client map `json_schema` sang cơ chế của provider (`response_format`+schema cho OpenAI/DeepSeek; `response_mime_type=application/json`+`response_schema` cho Gemini; tool-call cho Claude). Provider chính thức trả JSON đúng schema → "parse" chỉ là `json.loads` + validate Pydantic.

Schema yêu cầu (mỗi chunk):
```jsonc
{ "segments": [ { "index": <int>, "narration": "<thoại bằng [language]>",
                  "image_prompt": "<mô tả ảnh bằng TIẾNG ANH>", "image_label": "<slug-en>",
                  "image_query": "<3-7 từ khóa danh từ cụ thể, TIẾNG ANH>" } ] }
// narration bằng [language]; image_prompt LUÔN tiếng Anh (D1);
// image_query (OPTIONAL, backward-compatible): từ khóa tìm ẢNH THẬT dùng cho
//   provider ảnh web (web-commons) — vd "Atlantic horseshoe crab beach",
//   "red knot bird flock Delaware"; KHÔNG từ ngữ phong cách. Provider AI sinh ảnh bỏ qua.
// số segment = [count]; index từ [idx_start].
```

**Đường fallback — sentinel + parse robust.** Nếu provider/đời model không nhận `json_schema`, hoặc trả kèm văn bản thừa, system prompt thêm chỉ dẫn bọc JSON giữa `<<<REELO_SPEC>>>`…`<<<END_REELO_SPEC>>>`, rồi áp parse nhiều tầng dưới đây. (Đây là lưới an toàn, không còn là đường chính như khi dùng gemini-web2api.)

**Parse theo thứ tự (dừng ở bước đầu thành công):**
```python
def parse_chunk(raw: str) -> dict:
    block = (extract_between(raw, "<<<REELO_SPEC>>>", "<<<END_REELO_SPEC>>>")
             or extract_json_fence(raw)
             or extract_balanced_braces(raw))
    if block is None: raise ParseError("không tìm thấy block JSON")
    try:    return json.loads(block)
    except JSONDecodeError: return json.loads(repair_json(block))  # trailing comma, smart-quotes, strip prose
```

**Validate (gate "hợp lệ" thật) — Pydantic:**
```python
def validate_chunk(data, expected_count, idx_start) -> list[SegmentSpec]:
    segs = [SegmentSpec(**s) for s in data["segments"]]   # type + field
    if len(segs) != expected_count: raise ValidationError("sai số segment")
    if [s.index for s in segs] != list(range(idx_start, idx_start+expected_count)):
        raise ValidationError("index không liên tục/không khớp")
    for s in segs:
        if not s.narration.strip() or not s.image_prompt.strip():
            raise ValidationError("narration/image_prompt rỗng")
    return segs
```
> Validate bảo vệ **invariant cứng của skill**: mỗi segment ↔ đúng 1 block `===` ↔ đúng 1 ảnh. Module 2 dựa vào `count(===)==count(png)` nên đây là tuyến phòng thủ đầu.

**Retry policy:**
```
mỗi chunk: attempt 1..3 trên provider user chọn (json_schema bật từ attempt 1)
   ParseError/ValidationError -> thêm message sửa lỗi 1 dòng vào messages[]:
     "Phản hồi trước không hợp lệ: <lý do>. Trả lại CHỈ JSON đúng schema, [count] segment."
   nếu output bị cắt (JSON cụt) -> "Output bị cắt, rút ngắn narration mỗi segment"
vẫn fail -> lỗi UI: "Chưa lấy được kịch bản sạch cho tập này — thử lại / đổi provider"
```
- **Availability/key failure** (401/403/5xx/rate-limit) ≠ parse failure: xử lý ở `registry.resolve` (BYOK-aware fallback — [Module 3 §11](module-3-ai-service-manager.md)), không tính vào 3 lần parse.
- Vì mọi provider đều có JSON mode (M3-3), attempt 1 với `json_schema` gần như luôn pass; sentinel/parse robust chỉ là lưới an toàn.

---

## 10. Image style (D4) — preset + skill template, kết hợp

Map `inferStyle(referenceImages) -> {palette, description}` và [style.tsx](../reelo-ui/screens/style.tsx).

**Hai nguồn trực giao, ghép lại:**
- **Preset (visual)** — quyết định *phong cách vẽ*. Mỗi preset UI (cinematic/documentary/animated/minimal/vintage/noir) cần **1 `base_prompt` viết sẵn** (tiếng Anh) + `palette` + `description`.
- **Skill template (cấu trúc + bối cảnh)** — `skill.template.image.style_layer?` thêm lớp bối cảnh đặc thù (vd tradition cho religion: "first-century Galilee…"). Skill khác có thể không có style_layer.

```
image_prompt cuối (Module 2 ghép khi materialize) =
   preset.base_prompt
 + skill.template.image.style_layer   (nếu có)
 + segment.image_prompt               (scene-specific, AI sinh, tiếng Anh)
```

**Upload ref images:** UI gửi `File[]` (multipart) → `POST /style/infer` → client image/vision hoặc heuristic màu → `{palette, description}` → đổ vào `image_style` (đóng vai trò như một preset động).

`image_style` lưu trong `SeriesSpec` lúc approve; `aspect` (D8) cũng nằm trong `image_style`.

---

## 11. Schema `SeriesSpec` / `EpisodeSpec` (cập nhật)

```python
# reelo-backend/models/spec.py
class SegmentSpec(BaseModel):
    index: int               # 1-based; prefix tên file NN_
    narration: str           # thoại bằng SeriesSpec.language -> 1 block ===
    image_prompt: str        # scene-specific, TIẾNG ANH (D1)
    image_label: str         # slug-en đặt tên file
    image_query: str | None = None   # OPTIONAL: 3-7 từ khóa EN tìm ẢNH THẬT
                                     # (provider web-commons). None với provider AI sinh ảnh.

class EpisodeSpec(BaseModel):
    episode_id: str
    title: str
    order: int
    desc: str | None = None              # từ outline
    target_minutes: float | None = None  # kế thừa series, có thể override
    status: str = "draft"                # draft->scripted->assets->assembled->published
    youtube: dict | None = None          # {title, description, tags[]} — sinh lazy (D7)
    segments: list[SegmentSpec] = []     # RỖNG tới khi scripted (D2)
    # INVARIANT (khi scripted): index liên tục 1..len; len == segment_count suy từ density

class ImageStyle(BaseModel):
    preset_id: str
    base_prompt: str         # từ preset (visual)
    palette: list[str]
    description: str
    aspect: str = "16:9"     # D8: "16:9" | "9:16"
    style_layer: str | None = None   # từ skill template (D4)

class VoiceConfig(BaseModel):
    provider: str
    voice_id: str
    settings: dict | None = None

class SeriesSpec(BaseModel):
    schema_version: int = 1
    series_id: str
    name: str
    topic: str
    skill: str                       # religion|story|explain|news
    language: str                    # D1
    target_minutes: float            # D5 (mặc định mỗi tập)
    density: str                     # D5: light|standard|dense
    providers: dict                  # {script, image, voice}
    image_style: ImageStyle
    voice: VoiceConfig
    episodes: list[EpisodeSpec]
    music: dict | None = None        # {path, volume?, ducking?} — user upload per-series, optional (M2-1)
    subtitles: dict | None = None    # SRT luôn sinh (M2-2); field giữ để tắt/cấu hình sau
```

### Map lên UI ([reelo-ui/lib/data.ts](../reelo-ui/lib/data.ts))

| Spec | UI type | Ghi chú |
|---|---|---|
| `SeriesSpec` | `Series` | `cover` lấy từ ảnh đầu/label |
| `EpisodeSpec` | `Episode{status,dur?,views?}` | `status` track ở DB; `dur` điền sau render; tập mới = `draft` (chưa script) |
| `segments[]` | `ScriptSegment[]{id,text,img}` | `text=narration`, `img=image_prompt`; **rỗng tới khi scripted** → workspace gọi lazy gen khi mở |
| outline (Pha A) | `OutlineItem[]` | projection trước approve; approve dùng outline đã sửa (D6) |

---

## 12. Endpoints Module 1

| Method | Path | Map UI | Mô tả |
|---|---|---|---|
| `POST` | `/wizard/message` | `sendWizardMessage(topic, history)` | Pha A. Body `{idea, history[]}` (backend ghép `messages[]`) → `{reply, outline?}` |
| `POST` | `/wizard/approve` | (nút "Chốt & Lưu") | Pha B. Persist series shell từ outline đã sửa + config → `{series}` |
| `POST` | `/episodes/{id}/script` | (mở workspace lần đầu / trước produce) | Lazy gen segments + youtube meta (RULE+parse+validate, chunked) → `{episode}` (status→scripted) |
| `POST` | `/style/infer` (multipart) | `inferStyle(referenceImages)` | `{palette, description}` |
| `GET`/`POST`/`PUT` | `/series` | `listSeries`/`saveSeries` | CRUD; spec ở cột `spec_json` |

---

## 13. Sequence — lazy generation (điểm mới quan trọng nhất)

```
UI(workspace)     Backend /episodes/{id}/script    Module3 registry      script provider
   │ mở tập (segments rỗng)                              │                     │
   ├────────────────►│ derive segment_count(target,density)                    │
   │                 │ plan_chunks(n, skill.structure)   │                     │
   │                 │ for each chunk:                   │                     │
   │                 │   prompt + RULE + rule_prompt_extra│                     │
   │                 ├──── resolve(WRITE_SCRIPT) ───────►│ client              │
   │                 │     write_script(prompt) ─────────────────────────────►│
   │                 │◄──────────────────────────────────────────────────────┤ raw
   │                 │   parse_chunk -> validate (retry≤3, fallback)           │
   │                 │ reindex segments; gen youtube meta │                     │
   │                 │ status -> scripted; lưu spec_json  │                     │
   │◄────────────────┤ {episode with segments}            │                     │
   │ hiển thị editor (ScriptSegment[]) -> user sửa -> "Sản xuất" (Module 2)     │
```

---

## 14. Open questions còn lại (nhỏ, không chặn)

1. **Pha A chưa biết skill/language nếu user chưa qua Setup.** Đề xuất: Pha A dùng default (skill suy từ topic, language theo locale UI); Setup cho chỉnh lại trước approve. *Cần xác nhận thứ tự wizard → setup có cho phép điều này.* (Hiện flow UI: wizard → setup, nên Pha A trước, config sau — hợp lý.)
2. **WPM & giây/ảnh mỗi tier** là số đề xuất — tinh chỉnh sau khi đo thực tế (tốc độ đọc skill 0.92×).
3. **`SEGMENTS_PER_CHUNK`** (vd 8) cần test với gemini thực tế để cân bằng truncate vs số lượt.
4. **Mạch liên tục giữa chunk**: truyền tóm tắt cuối chunk trước vào `messages[]` của chunk sau (không còn `conversation_id`) — chốt khi đo chất lượng.
5. **`image_label` slug**: AI sinh (kèm trong JSON) hay backend slugify từ vài từ đầu `image_prompt`? Đề xuất: AI sinh, backend sanitize/đảm bảo unique + zero-pad.
6. **Số tập tối đa / series**: không cap cứng; cảnh báo mềm nếu quá lớn (chi phí).

---

## Liên kết
- Client `write_script` + fallback + JSON mode: [module-3-ai-service-manager.md](module-3-ai-service-manager.md)
- Tiêu thụ `SeriesSpec`/`segments`, materialize, **sửa pipeline bỏ cap 8 (D3)**, aspect (D8): [module-2-video-generator.md](module-2-video-generator.md)
- Persistence (lưu khi approve), skill template `rule_prompt_extra`/`style_layer`: [integration.md](integration.md)
