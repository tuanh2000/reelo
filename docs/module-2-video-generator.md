# Module 2 — Video Generator

> Từ `SeriesSpec` đã có script (Module 1) → dựng 4 yếu tố (lời thoại, hình ảnh, voice, **nhạc nền optional**) + **SRT** + **thumbnail** → ráp thành `final.mp4`. Tái dùng *client* voice/image qua [Module 3](module-3-ai-service-manager.md) và *ý tưởng* timing/aesthetic của skill, nhưng bước **render là Reelo-native** (do Ken Burns + ducking + aspect + N ảnh lớn).

Phụ thuộc: [Module 1](module-1-ai-chatting.md) (input + lazy script gen) và [Module 3](module-3-ai-service-manager.md). Map UI: `startGeneration -> {jobId}`, `pollGeneration(jobId) -> GenJob[]` ([reelo-ui/lib/api.ts](../reelo-ui/lib/api.ts), [workspace.tsx](../reelo-ui/screens/workspace.tsx)).

---

## 0. Quyết định đã chốt (decisions log)

| # | Quyết định | Hệ quả thiết kế |
|---|---|---|
| M2-1 | **Nhạc nền: user upload, per-series, optional** | Field `music` series-level; auto **ducking** dưới giọng + **loop**; không có cũng OK |
| M2-2 | **Phụ đề: xuất SRT riêng kèm mp4** (không burn-in) | Module mới `subtitles.py`; auto-timed từ segment |
| M2-3 | **Chuyển động: Ken Burns nhẹ (zoom/pan) tự động** | Đổi filtergraph per-clip sang `zoompan`; không dùng được clip tĩnh của skill |
| M2-4 | **Thumbnail: AI sinh 3 ứng viên lúc render xong** | Module mới `thumbnail.py`; dùng image client + prompt thumbnail; user chọn ở Review |
| M2-5 | **Free path: build voice free (Edge-TTS)** | Thêm client `EdgeTTSClient` (Module 3) → đường free trọn vẹn (gemini+kie+edge) |
| M2-6 | **TTS dài: chunk theo phần + ghép mp3** | Voice orchestration chia đoạn dưới giới hạn ký tự, TTS từng đoạn, concat |
| M2-7 | **Ảnh lỗi (sau retry): chặn render** | Parent job `error`, giữ asset, user retry ảnh lỗi; giữ invariant count |
| M2-8 | **Serialize tập, song song ảnh 3-4, render=1, cảnh báo chi phí** | Semaphore ảnh; queue tập; ước tính chi phí trước produce |
| M2-9 | **Sản xuất per-episode** (không batch series ở v1) | `startGeneration` theo episode; khớp workspace |
| M2-10 | **Giữ toàn bộ project folder** sau render | Không dọn intermediate; retry/sửa rẻ |
| M2-11 | **Nguồn ảnh web (ẢNH THẬT) là provider chính thức** — `web-commons` keyless | Khác biệt thị trường; `SegmentSpec.image_query` (EN) dẫn search; attribution PD/CC0/CC-BY lưu `credits.json` (pháp lý) |
| M2-12 | **Curate ảnh web do CON NGƯỜI chọn** — provider `web-*` đưa ~9 ứng viên/đoạn để user tự chọn (không auto-pick) | Bước RIÊNG giữa scripted→render; chỉ web-photo (`supports_candidates`); AI giữ auto. Lưu `Episode.image_curation` (JSONB, NGOÀI SeriesSpec). `search_candidates`→cache; `download_chosen` tải ảnh đã chọn lúc render; thiếu chọn→fallback auto. Không enum status mới. |
| M2-13 | **Trộn ẢNH + VIDEO CLIP thật per-đoạn** — lưới curate gộp ảnh `web-commons` + clip `web-pexels` (BYOK, CC0); mỗi đoạn user chọn 1 media (ảnh HOẶC clip) | `ImageCandidate`→`MediaCandidate` (thêm `media_type`/`duration`/`poster_url`/`preview_url`/`video_url`; alias giữ ngược). Provider `web` (aggregate) gộp mọi `web-*` khả dụng; không key Pexels → chỉ ảnh (graceful). Mỗi candidate ghi `provider` nguồn → runner `download_chosen` đúng client. **Render clip:** scale=increase+crop về khung, **cắt khớp thời lượng đoạn** (từ đầu; clip ngắn hơn → `-stream_loop -1` loop), **`-an` tắt tiếng gốc** (giữ narration+nhạc), bỏ Ken Burns cho đoạn video (ảnh vẫn Ken Burns), fps=30/libx264/crf20/yuv420p — đồng nhất với clip ảnh → xfade + mux NGUYÊN như cũ. Pexels chọn file mp4 gần khung nhất (HD/FHD). Attribution clip vào `credits.json`. |
| M2-14 | **Voice CLONE (OmniVoice) — chế độ clone của voice stage** | `VoiceConfig.mode` ∈ `preset`(mặc định)\|`clone`; `clone` dùng `voice_sample={audio_key,transcript,language}` (NGOÀI key, Reelo-hosted GPU). Khi `voice.provider=omnivoice`: orchestration tải sample từ object storage 1 lần → thread `ref_audio`/`ref_text`/`language` vào MỖI chunk `VoiceRequest`; chunk+concat NGUYÊN như cũ. Edge/eleven (preset) bỏ qua field clone (backward-compatible). Upload mẫu: `POST /series/{id}/voice-sample` (audio+transcript+language) → ffmpeg chuẩn hóa wav 24kHz mono (validate 3–30s) → lưu `voice-samples/<user>/<series>/sample.wav` → set `spec.voice` clone. |
| D3 (M1) | **Bỏ cap 8 ảnh** | N ảnh suy từ `target_minutes×density`; render-by-clip + concat chịu N lớn |
| D8 (M1) | **Aspect 16:9 / 9:16 per series** | Khung render + `--size` ảnh theo `image_style.aspect` |

> **Lệch khỏi nguyên tắc "skill black-box":** M2-3/M2-1/M2-2/D8 buộc thay bước merge. Ta giữ **2 client subprocess** (ảnh kie, voice eleven) + **ý tưởng** của skill (timing word-count, nền blur-pillarbox), nhưng **viết renderer Reelo-native** (`render.py`) thay cho `merge_video.py`. Xem §5 và §15.

---

## 1. Mục tiêu & nguyên tắc

- **Tái dùng client, tự render.** Sinh ảnh = `generate_image.py` (kie) qua Module 3; sinh voice = ElevenLabs (`generate_voice.py`) hoặc Edge-TTS (free). Ráp = **renderer Reelo-native** kế thừa ý tưởng của [merge_video.py](../skill-tao-video-Youtube-ton-giao/scripts/merge_video.py).
- **Source-of-truth là thư mục project** với `script.md` (`===`) + `images/NN_*.txt|png`, cộng `voice/`, `music/`, `subs.srt`, `thumbnails/`, `final.mp4`. SaaS: worker dựng ở **local temp** rồi **upload object storage** `projects/<user_id>/<episode_id>/`; web phục vụ qua signed URL (xem [integration.md §2/§5](integration.md)).
- **Job server, không streaming.** UI poll `GenJob[]` ~400ms; state ở Postgres.
- **Đường free trọn vẹn**: gemini (script) + kie (image) + Edge-TTS (voice) — không cần key.

---

## 2. Input — `SeriesSpec` / `EpisodeSpec` (đã scripted)

`startGeneration` nhận `(seriesId, episodeId)`. **Bước 0:** nếu `episode.segments` rỗng → gọi `Module 1.generate_episode_script(...)` (status `draft→scripted`, [Module 1 §7](module-1-ai-chatting.md)). Sau đó dùng:
- `episode.segments[]` (narration + image_prompt + image_label + index)
- `series.image_style` (base_prompt + style_layer + palette + **aspect** D8)
- `series.voice` (provider + voice_id + settings)
- `series.language` (cho SRT + chọn giọng Edge-TTS)
- `series.music?` (path track user upload — M2-1; optional)

---

## 3. Materializer → `projects/<date>_<slug>/`

```
projects/2026-06-09_my-episode/
├── script.md                  # narration join "\n\n===\n\n"
├── images/  NN_label.txt + NN_label.png
├── voice/   voice_part_01.mp3 ... + voice.mp3 (đã concat) + voice_metadata.json
├── music/   bg.mp3            # copy từ track user upload (nếu có)
├── subs.srt                   # M2-2
├── thumbnails/ thumb_1.png thumb_2.png thumb_3.png   # M2-4
└── final.mp4
```

```python
def materialize(series, ep, tmpl, preset, root) -> Path:
    folder = root / f"{today}_{slugify(ep.title)}"
    (folder/"images").mkdir(parents=True); (folder/"voice").mkdir(); (folder/"thumbnails").mkdir()
    # script.md: đảm bảo count(===)+1 == len(segments) == count(images)
    (folder/"script.md").write_text("\n\n===\n\n".join(s.narration for s in ep.segments))
    # images/NN_label.txt = preset.base_prompt + style_layer + segment.image_prompt  (D4)
    for s in ep.segments:
        prompt = "\n\n".join(filter(None, [preset.base_prompt, series.image_style.style_layer, s.image_prompt]))
        (folder/"images"/f"{s.index:02d}_{s.image_label}.txt").write_text(prompt)
    if series.music and series.music.get("path"):
        copy(series.music["path"], folder/"music"/"bg.mp3")
    return folder
```
**Invariant cứng:** `count(===)+1 == len(segments) == count(*.png)`; tên file zero-padded khớp thứ tự segment ↔ thứ tự hiển thị.

---

## 4. Bốn yếu tố — chi tiết

### 4.1 Lời thoại (narration)
Đã có sẵn trong `segments[].narration` (Module 1). Ghi ra `script.md` (mỗi segment 1 block `===`). Là nguồn cho cả voice (text) lẫn timing (word-count) lẫn SRT.

### 4.2 Voice / TTS — chunk + concat (M2-5, M2-6)
**Provider:** `series.voice.provider` resolve qua Module 3:
- `eleven` (key) → client bọc `generate_voice.py`.
- `edge` (free, **mới** — M2-5) → `EdgeTTSClient` pure-Python (gói `edge-tts`), giọng theo `language` (vd `vi-VN-HoaiMyNeural`, `en-US-AndrewNeural`).
- `omnivoice` (CLONE, **M2-14**) → `OmniVoiceClient` POST tới microservice GPU `{OMNIVOICE_URL}/clone`; KHÔNG dùng `voice_id` mà clone từ `voice.voice_sample`. Khi `voice.mode=="clone"`: `synth_voice` tải sample về `voice/ref_sample.wav` 1 lần (`_prepare_clone`) rồi gán `req.ref_audio/ref_text/language` cho mỗi chunk; client trả wav 24kHz → transcode mp3. Concat/timing y hệt.

**Chunk cho video dài (M2-6):** ElevenLabs giới hạn ký tự/lượt; script 25' vượt. Voice orchestration:
```python
async def synth_voice(folder, series, ep, client):
    parts = split_by_char_limit(read_sections(folder/"script.md"), limit=client.char_limit)  # gộp section tới ngưỡng
    paths = []
    for i, text in enumerate(parts, 1):                 # tuần tự để giữ nhịp/giọng
        p = folder/"voice"/f"voice_part_{i:02d}.mp3"
        await client.generate_voice(VoiceRequest(text=text, voice_id=series.voice.voice_id,
                                                 settings=series.voice.settings), out_path=p)
        paths.append(p)
    ffmpeg_concat(paths, folder/"voice"/"voice.mp3")    # concat demuxer
    dur = ffprobe_duration(folder/"voice"/"voice.mp3")
    return dur
```
> Chunk **trong suốt** với timing: timing ảnh vẫn tính từ word-count × tổng duration `voice.mp3`. Drift nhỏ (±1-3s) chấp nhận. `VoiceRequest` mở rộng nhận `text` trực tiếp (ngoài `text_file`) cho chunk.

### 4.3 Hình ảnh & Video clip (M2-7, M2-11, M2-12, M2-13, D3, D8)
- 1 media / segment, N lớn (D3). Provider `series.providers.image` qua Module 3 `generate_image`, `size` theo `aspect` (D8).
- **Ba loại nguồn media (không chỉ AI):**
  - *Generative* (kie/gemini/openai/sd) — sinh ảnh từ `image_prompt` đã ghép (preset+style_layer+scene).
  - *Web-photo* (**web-commons**, keyless) — tìm **ẢNH THẬT** PD/CC0/CC-BY trên Wikimedia Commons theo `segment.image_query` (nếu rỗng → de-slug `image_label` → vài từ đầu prompt). Điểm khác biệt: tài liệu/khoa học/lịch sử thật, không phải ảnh AI giống nhau.
  - *Web-video* (**web-pexels**, BYOK key Pexels, CC0-like) — tìm **CLIP VIDEO THẬT** trên Pexels Video API theo cùng `image_query`; trả `MediaCandidate(media_type="video")`. Xem [Module 3 §3](module-3-ai-service-manager.md).
- Runner truyền `ImageRequest.query = segment.image_query or deslug(image_label)` + `label = image_label`. Generative client bỏ qua `query`; web client dùng nó. Tránh trùng giữa segment trong 1 tập qua `ctx.extra["commons_used"]` (set id đã dùng).
- **Curate media do CON NGƯỜI chọn (M2-12 + trộn ảnh/clip M2-13)** — chỉ provider `web-*` (`client.supports_candidates`):
  - Provider image `web` (aggregate) gộp **mọi** `web-*` khả dụng cho user (`web-commons` luôn keyless; `web-pexels` chỉ khi user có key Pexels). Cũng hỗ trợ chọn riêng `web-commons` / `web-pexels`. Helper `curation.web_media_providers(registry, provider_id, ctx)` liệt kê client khả dụng (keyless trước → ảnh là default).
  - Trước khi produce, UI gọi `GET /episodes/{id}/image-candidates`: với mỗi đoạn gọi `search_candidates(query, size, limit)` trên TỪNG provider rồi **merge** (cap ~6 ảnh + ~6 clip/đoạn; chỉ metadata + `thumb_url`/`poster_url`, **không tải file lớn**). Mỗi candidate ghi `provider` nguồn. Lưu cache `Episode.image_curation` (JSONB), mặc định `chosen_id = candidate[0]` (ảnh đầu). Gọi lại → trả cache. Provider AI → 409. Không key Pexels → lưới chỉ ảnh (graceful).
  - `POST /episodes/{id}/image-selection` `{selections:{segment_index:candidate_id}}` validate id thuộc cache rồi cập nhật `chosen_id` (chọn clip hoạt động hệt chọn ảnh).
  - Lúc produce, runner đọc `image_curation`; mỗi đoạn có `chosen` → resolve `provider` nguồn → `src_client.download_chosen(candidate, out)`: ảnh tải raster lớn (`full_url`); clip tải `.mp4` (`video_url`) vào `images/NN_<label>.mp4`. Đoạn không có chọn → fallback auto `generate_image`. Curate là dữ liệu pre-produce ở status `scripted` — **không** thêm enum status, **không** nhét vào `SeriesSpec` canonical.
- **Render media-aware (M2-13):** `render_episode(media_paths, ..., media_types=[...])` dispatch per-clip — ảnh → `build_clip_cmd` (Ken Burns + blur-pillarbox); video → `build_video_clip_cmd` (scale=increase+crop về WxH, **`-t` cắt khớp thời lượng đoạn từ đầu**, nguồn ngắn hơn → **`-stream_loop -1`** loop cho đủ, **`-an` tắt tiếng gốc**, fps=30/libx264/crf20/yuv420p). Cả hai ra `clip_NN.mp4` đồng nhất → **xfade chain + mux audio (narration + nhạc nền) NGUYÊN như cũ**; padding crossfade áp cho cả clip video.
- **Attribution (M2-11):** web client trả `{title, author, license, source_url, descriptionurl}` trong `ImageResult.raw["attribution"]` (`generate_image` + `download_chosen`, kèm `media_type`). Runner gom vào `credits.json` (upload kèm asset, key `paths["credits"]`) — **yêu cầu pháp lý** khi SaaS tái dùng ảnh CC-BY/PD + clip Pexels.
- Song song với Semaphore 3-4 (M2-8).
- **Failure policy (M2-7):** retry trong client (skill đã retry 3×; web client retry nhẹ + fallback query, không tìm được ảnh hợp lệ → `ProviderUnavailableError`); nếu vẫn fail → child job `error`, **chặn render**, parent `error`. Giữ asset đã có; user retry ảnh lỗi (UI có nút retry per-job).

### 4.4 Nhạc nền (M2-1) — optional, user upload, ducking + loop
- `series.music.path` (upload per-series ở Setup/Style). Không có → bỏ qua, video chỉ có voice.
- Khi render: **loop** nhạc cho đủ độ dài + **sidechain ducking** (giảm volume nhạc khi có giọng). Tham số mặc định: `music_volume≈0.25`, ducking ratio cao. Tinh chỉnh sau.

---

## 5. Renderer Reelo-native (`render.py`) — thay `merge_video.py`

Kế thừa cấu trúc 2 pha của skill (render từng clip → xfade chain) nhưng thêm **Ken Burns** + **aspect** + **music ducking**.

**Khung đích theo aspect (D8):** `16:9 → 1920×1080`; `9:16 → 1080×1920`.

**Pha 1 — render từng clip với Ken Burns (M2-3):**
```bash
# duration d_i = (words_i / total_words) * audio_duration   (giữ thuật toán skill)
ffmpeg -y -loop 1 -t {d_i} -i images/NN.png -filter_complex "
  [0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},
       gblur=sigma=30,eq=brightness=-0.25,fps=30[bg];
  [0:v]scale={W*2}:-1,
       zoompan=z='min(zoom+0.0008,1.20)':d={d_i*30}:x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':
       s={fgW}x{fgH}:fps=30[fg];                         # Ken Burns: zoom chậm tới 1.2x
  [bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p[v]
" -map "[v]" -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p -r 30 -t {d_i} clip_NN.mp4
```
- Hướng zoom/pan **luân phiên** theo index (in/out, trái/phải) để đỡ đơn điệu.
- Nền blur-pillarbox giữ từ skill (đẹp với mọi tỉ lệ ảnh nguồn).

**Pha 2 — xfade chain + audio (voice + ducked music):**
```bash
ffmpeg -y -i clip_00.mp4 ... -i clip_{N-1}.mp4 \
  -i voice/voice.mp3 [-stream_loop -1 -i music/bg.mp3] \
  -filter_complex "
    [0:v][1:v]xfade=transition=fade:duration=0.6:offset={o1}[v1]; ... [v{N-1}]   # video
    # audio (nếu có nhạc):
    [{music_idx}:a]volume=0.25,aloop=loop=-1:size=...[m];
    [m][{voice_idx}:a]sidechaincompress=threshold=0.03:ratio=8:release=300[md];   # duck nhạc theo giọng
    [{voice_idx}:a][md]amix=inputs=2:duration=first:dropout_transition=0[a]
  " -map "[v{N-1}]" -map "[a]" -c:v libx264 -preset medium -crf 20 -pix_fmt yuv420p \
    -c:a aac -b:a 192k -shortest final.mp4
```
> Không có nhạc → `-map voice` trực tiếp (như skill). Filtergraph minh hoạ; cần tinh chỉnh tham số ducking/zoom.

**Render-by-clip + concat chịu N lớn (D3):** vì render từng clip rồi xfade, chuỗi xfade dài N-1 bước. **Verify** với N lớn (vài chục clip) — nếu filtergraph quá dài/chậm, fallback: xfade theo batch (vd nhóm 10 clip → segment.mp4) rồi concat các segment. Ghi nhận ở §16.

---

## 6. Subtitles SRT (`subtitles.py`, M2-2)
- Auto-timed: dùng cùng phân bổ word-count × duration để gán thời gian mỗi segment; chia nhỏ theo **câu** trong narration cho dễ đọc (ước lượng thời gian câu theo tỉ lệ từ).
- Xuất `subs.srt` cạnh `final.mp4` (không burn-in). YouTube/Reelo dùng làm caption.
- Ngôn ngữ = `series.language`.
- Bước rẻ (text-only) → gộp vào job `render` (không tạo job riêng).

## 7. Thumbnail (`thumbnail.py`, M2-4)
- **Sau khi render xong**, sinh **3 ứng viên** qua image client (`series.providers.image`) với **prompt thumbnail tự động** dựng từ `episode.title` + chủ đề + `image_style` (palette/preset). Lưu `thumbnails/thumb_{1..3}.png`.
- User chọn 1 ở màn Review (`PublishMeta.thumbnailIndex`).
- Là 1 child job riêng (`thumbnail`), chạy sau `render`.
- *Tương lai:* có thể nâng theo phong cách skill thumbnail-bible (watercolor + typography). v1 dùng prompt thumbnail đơn giản.

---

## 8. Async job model (M2-8, M2-9)

> **Cập nhật SaaS:** job chạy trên **Arq worker (Redis)**, state ở **Postgres**, asset trên **object storage** (không còn in-process asyncio/SQLite của bản local-first). Web chỉ **enqueue**; một episode = **1 Arq task** `produce_episode(user_id, episode_id)`. *Bên trong* task, voice + N ảnh vẫn chạy song song bằng `asyncio.gather` + Semaphore (code §3 chính là thân Arq task).

**`startGeneration(seriesId, episodeId) -> {jobId}`** (per-episode — M2-9):
1. Bước 0: đảm bảo `scripted` (lazy gen nếu cần).
2. Tạo parent job + seed child `GenJob` (1:1 với UI, list động):
```
parent(episode)
 ├─ voice                 (1 job; nội bộ chunk+concat — M2-6)
 ├─ image_1 .. image_N    (N job — D3)
 ├─ render                (join: chờ voice + mọi image done; gồm cả SRT — M2-2)
 └─ thumbnail             (sau render — M2-4)
```
`GenJob{id,name,icon,state:queued|running|done|error,progress}`.

**Thực thi & thứ tự:**
```
voice ∥ image_1..N    (song song; Semaphore(3-4) cho image — M2-8)
   → (mọi child done?)  render  (cap FFmpeg = 1 — M2-8)
                          → thumbnail
ảnh nào error sau retry → chặn render (M2-7), parent error
```
- **Serialize tập** (M2-8/M2-9): runner xử lý 1 episode/lần (không produce nhiều tập song song ở v1).

**Progress (coarse, trung thực):** queued→0; start→~10; chạy→climb theo elapsed cap ~90; done→100; exit≠0→error (lưu stderr). Episode progress = done_children/total.

**`pollGeneration(jobId) -> GenJob[]`:** đọc child rows từ Postgres (pure read, chịu 400ms).

---

## 9. Ước tính & cảnh báo chi phí trước produce (M2-8)
Trước khi chạy, tính & hiển thị:
- Số ảnh `N` (× đơn giá kie.ai ≈ chi phí ảnh).
- Số ký tự voice (× đơn giá ElevenLabs nếu dùng key; Edge-TTS free = 0).
- 3 ảnh thumbnail.
- Ước lượng thời gian render.
→ UI confirm trước khi `startGeneration` thực thi (đặc biệt quan trọng với video dài/Dense — D3).

---

## 10. Retry / resume / lưu trữ (M2-7, M2-10)
- **Retry per-job:** chạy lại 1 child (ảnh/voice) + re-trigger render. Asset là file, **không xoá khi fail dở** → idempotent. Khớp story retry của skill.
- **Resume:** `status` (Postgres) + asset (object storage) = state. Worker/web restart giữa render → mark job `running`→`error` để retry; không orphan.
- **Giữ toàn bộ project folder (M2-10):** không dọn intermediate (lưu object storage `projects/<user_id>/<episode_id>/`); retry/sửa/debug rẻ.

---

## 11. Status transitions (map `EP_STATUS`)
```
draft     ──lazy script gen (Module 1)──►  scripted
scripted  ──startGeneration──────────────►  assets
assets    ──render + thumbnail done──────►  assembled
assembled ──publish (v1: export)─────────►  published
```

---

## 12. Publish v1 — chỉ export
`publishToYouTube(seriesId, episodeId, meta)` v1 **không** upload. Trả:
```jsonc
{ "videoPath": ".../final.mp4",
  "srtPath": ".../subs.srt",
  "thumbnailPath": ".../thumbnails/thumb_{chosen}.png",
  "metadata": { title, description, tags }   // từ EpisodeSpec.youtube + lựa chọn ở Review
}
```
User tự upload. (Auto-upload OAuth/Data API = roadmap — risk #6 [integration.md](integration.md).)

---

## 13. Endpoints
| Method | Path | Map UI | Mô tả |
|---|---|---|---|
| `POST` | `/generation/start` | `startGeneration` | Bước 0 + tạo jobs + kick runner → `{jobId, costEstimate}` |
| `GET` | `/generation/{jobId}` | `pollGeneration` | `GenJob[]` |
| `POST` | `/generation/{jobId}/retry/{childId}` | (retry per-job) | Chạy lại 1 child + re-trigger render |
| `POST` | `/series/{id}/music` (multipart) | (upload nhạc, Setup/Style) | Lưu track per-series → `series.music.path` |
| `POST` | `/publish/export` | `publishToYouTube` | Trả paths + metadata |

---

## 14. Sequence
```
UI workspace      Backend /generation        Module3            kie / eleven|edge / FFmpeg
   │ produce          │ bước 0: ensure scripted (Module 1 nếu cần)                  │
   ├─────────────────►│ materialize → projects/<...>/                               │
   │                  │ seed jobs (voice + N img + render + thumbnail)              │
   │                  │ voice (chunk+concat) ∥ image_1..N (Sem 3-4) ──resolve──────►│
   │◄── poll GenJob[] ┤ progress cập nhật Postgres                                    │
   │                  │ (mọi child done) render.py: KenBurns+xfade+duck → final.mp4 │
   │                  │ subtitles.py → subs.srt ; thumbnail.py → 3 ảnh              │
   │                  │ status → assembled                                          │
   │◄─────────────────┤ done                                                        │
```

---

## 15. Tóm tắt: tái dùng vs viết mới
| Thành phần | Cách làm |
|---|---|
| Sinh ảnh (kie) | **Tái dùng** `generate_image.py` qua Module 3 client |
| Sinh voice (ElevenLabs) | **Tái dùng** `generate_voice.py` (bọc) + orchestration chunk/concat (mới) |
| Sinh voice (Edge-TTS free) | **Mới** `EdgeTTSClient` (Module 3, M2-5) |
| Ráp video | **Mới** `render.py` Reelo-native (Ken Burns + aspect + ducking) — thay `merge_video.py`; kế thừa timing word-count + nền blur-pillarbox |
| SRT | **Mới** `subtitles.py` |
| Thumbnail | **Mới** `thumbnail.py` (dùng image client) |
| Timing ảnh | **Tái dùng thuật toán** skill: `(words_i/total)×audio_dur` |

---

## 16. Open questions còn lại
1. **Verify render-by-clip + xfade với N lớn** (vài chục clip): nếu chậm/ngốn RAM → batch xfade rồi concat. Cần benchmark.
2. **Tham số Ken Burns** (tốc độ zoom 0.0008, max 1.2×, hướng luân phiên) + **ducking** (volume 0.25, ratio 8) là đề xuất — tinh chỉnh bằng thử nghiệm.
3. **Edge-TTS voice mapping** theo `language` (giọng VI/EN mặc định) + char_limit mỗi provider để chunk.
4. **Đơn giá** kie/eleven cho ước tính chi phí — cần số thực để hiển thị (§9).
5. **Concat voice** có chèn khoảng lặng nhỏ giữa chunk không (tránh nối cụt câu)?
6. **Thumbnail prompt** dựng tự động ra sao (title + palette) — chốt template khi triển khai; có thể nâng theo skill thumbnail-bible sau.
7. **Nhạc nền re-mux khi đổi:** nếu user đổi nhạc sau render, cần re-mux audio (rẻ, không render lại video) — endpoint riêng?

---

## Liên kết
- Input `SeriesSpec`/segments + lazy gen: [module-1-ai-chatting.md](module-1-ai-chatting.md)
- Client image/voice (eleven + **edge-tts mới**) + fallback + thumbnail dùng GENERATE_IMAGE: [module-3-ai-service-manager.md](module-3-ai-service-manager.md)
- Subprocess helper, renderer Reelo-native vs skill, persistence, risks: [integration.md](integration.md)
