---
name: kich-ban-ton-giao
description: End-to-end production pipeline for scholarly English YouTube videos about religion (Buddhism, Christianity, Islam, Hinduism, Judaism, Sikhism, Taoism, Jainism, etc.) targeted at practicing believers. Four-step flow: write script with === markers between image segments → generate voice (ElevenLabs) and images (kie.ai gpt-image-2 in soft painterly contemporary devotional-illustration style) in parallel → assemble into a 1920×1080 MP4 with image timing auto-calculated from word distribution × actual audio duration. Use this skill whenever the user asks for YouTube religion content, sacred text explanations, scripture commentary, dharma talks, sermon videos, religious explainer videos, or anything requiring synchronized script + voice + religious imagery — even if they don't explicitly mention "skill" or "pipeline".
---

# kich-ban-ton-giao — Religious YouTube Production Pipeline

This skill produces a complete YouTube-ready folder for a single religious video. Each output folder contains:

1. The script (`script.md`) — voice text with `===` markers between image segments
2. A voice-acted MP3 from ElevenLabs (`voice/voice.mp3`)
3. 2-8 soft painterly contemporary-devotional-illustration images from kie.ai's `gpt-image-2-text-to-image` (`images/*.png`)
4. **`final.mp4`** — auto-merged 1920×1080 video assembled by FFmpeg, ready to upload to YouTube. Image timing is auto-calculated from word distribution × actual audio length.

The audience is **already a believer or practitioner**. Write to deepen their understanding, not to convert or summarize for outsiders.

---

## Quick start — FOR CLAUDE, read this first

When the user invokes this skill (or asks for a religious YouTube video), follow this exact sequence. Each step has a dedicated section below with details.

1. **Setup check** — verify `.env`, FFmpeg, Python deps (one-time)
2. **Topic + script** — get topic and length; write `script.md` with `===` markers between image segments; write image prompts; show for review
3. **Generate assets** — call `generate_voice.py` once and `generate_image.py` in parallel per image
4. **Assemble** — call `merge_video.py` — it auto-calculates image timing from word distribution × actual audio duration. No manual timing file.
5. **Report** — folder path + files produced

Never skip the review in step 2 without explicit user instruction — a bad script wastes the API calls that follow.

---

## Step 1 — Setup on first use

Run these checks the first time the user invokes the skill. Cache the results — re-checking on every invocation slows the workflow.

### 1a. API keys (.env)

Check whether `<skill-root>/.env` exists. If it does, load it and continue. If not, prompt the user for these values, then write `.env` from `.env.example`:

- `ELEVENLABS_API_KEY` — get from elevenlabs.io → Profile → API Keys
- `KIE_API_KEY` — get from kie.ai → API Keys
- `VIDEOS_OUTPUT_DIR` (optional override) — where to save video folders. **Default: `<skill-root>/projects/`** — each video gets its own subfolder inside the skill, keeping everything related to one place. Only override if you want videos saved elsewhere.

If a key is missing, explain what each one is for and how to get it. Don't generate fake keys or proceed — voice and image generation will fail without them.

### 1b. Python dependencies

Run `python -c "import requests, dotenv"`. If it fails, run `pip install -r requirements.txt` from the skill root.

### 1c. FFmpeg

Run `ffmpeg -version`. If it fails, FFmpeg isn't installed or isn't on PATH. Direct the user to `references/ffmpeg-setup.md`. The short version:

- Windows: `winget install ffmpeg` (then open a new terminal)
- macOS: `brew install ffmpeg`
- Linux: `sudo apt install ffmpeg` (or equivalent for the distro)

FFmpeg is a local tool, **not a cloud service** — no API key, no account. Once installed it just works.

---

## Step 2 — Capture the topic

Ask for two things if the user didn't supply them:

- **Topic** — the specific text, teaching, doctrine, or question. Anchor in a primary source when possible (e.g., "Heart Sutra on form-emptiness" not just "Buddhism").
- **Target length** — short (<5 min), medium (5-10 min), long (10-20 min).

If the topic is vague ("do something on Buddhism"), propose 3-5 specific title options before continuing. Good titles for this niche:

- Promise scholarly depth, not surface summary
- Name the specific text or teaching when possible
- Stay under ~70 characters for YouTube display
- Avoid clickbait phrasing ("SHOCKING", "You Won't Believe") — practitioners associate this with shallow content

---

## Step 3 — Write the script (the 3-layer method)

Every script follows this five-part structure. Word targets assume ~150 spoken words per minute.

### 1. Opening Hook (5-10%)
Open with the *practitioner's puzzle*, not a definition. A question they've privately wondered, a verse that seems to contradict another, a teaching harder than it first appears. The hook earns the next 8 minutes by promising this tension will be resolved. Avoid "Today we're going to talk about..." openings.

### 2. Context (15-20%)
Set the textual and historical anchor: which text, which tradition, which moment. Name the original language (Pali, Koine Greek, Classical Arabic, Sanskrit, Hebrew, etc.) and the key terms that will recur. Practitioners want the primary source, not paraphrases.

### 3. Three Layers of Meaning (60-70% — the heart)

Do all three layers, in order, with clear verbal transitions ("So that's the literal sense. Now — what does the tradition do with this?"). Skipping the middle layer is the single most common failure of religious YouTube.

**Layer 1 — Literal / Textual** (*zahir*, *peshat*, *shabda*, etc.)
What does the text literally say in its original language and immediate context? Translation choices, grammatical structure, original audience meaning.

**Layer 2 — Theological / Doctrinal**
How does the tradition read this? Where does it sit in the doctrinal architecture? Name specific commentators:
- Buddhism: Buddhaghosa, Nāgārjuna, Asaṅga, Dōgen, Tsongkhapa
- Christianity: Augustine, Aquinas, Origen, Luther, Calvin, Barth
- Islam: Ibn Kathīr, al-Ghazālī, al-Rāzī, Ibn ʿArabī
- Hinduism: Śaṅkara, Rāmānuja, Madhva, Abhinavagupta
- Judaism: Rashi, Maimonides, Nachmanides, the Zohar tradition

This layer signals "I take your tradition seriously."

**Layer 3 — Practical / Lived**
How does someone actually live this? Not a sentimental application — a concrete one. Use the tradition's own practical vocabulary (*sādhanā*, *mitzvot*, *iḥsān*, *sīla*, etc.).

### 4. Closing (10-15%)
Restate the puzzle from the hook and show how the layered reading resolved it. End with a question that lives past the video — something to sit with, not an action item.

### Tone rules across the whole script

- Treat the tradition's claims on their own terms during this video
- Use specialist vocabulary, but define each term the first time
- Cite passages by reference (book/chapter/verse, sutra name, surah and ayah) so practitioners can verify
- No filler ("at the end of the day," "if you really think about it")
- Write for the ear, not the eye — short sentences, no academic hedging clutter

---

## Step 4 — Calculate image count

Calculate from word count. Place images at structural beats, never fixed time intervals.

| Length | Word count | Image count |
|---|---|---|
| <5 min | <750 | 2-3 |
| 5-10 min | 750-1500 | 3-5 |
| 10-15 min | 1500-2250 | 5-7 |
| 15-20 min | 2250-3000 | 7-8 |

**Hard cap: 8 images.** More creates a slideshow feel.

**Placement priority order:**
1. One image for Opening Hook
2. One image for Context
3. One image per Layer (literal / theological / practical)
4. One image for Closing
5. Extra images go to whichever Layer is doing the most conceptual work

---

## Step 5 — Generate image prompts (BASE STYLE + details)

Every image prompt starts with the BASE STYLE block, then adds a tradition-specific layer, then scene specifics. This keeps the video visually unified.

### BASE STYLE (paste verbatim at the start of every prompt)

```
Soft painterly realism in the tradition of contemporary devotional illustration (similar to modern Bible-illustration art by Greg Olsen or Liz Lemon Swindle), naturalistic detail with a warm earth-tone palette (ochre, cream, deep terracotta, muted lapis blue, sage green), atmospheric lighting evoking golden hour or soft overcast daylight, cinematic widescreen landscape composition with depth and atmospheric perspective, expressive faces with authentic period clothing for the tradition, real-world settings (rolling hills, water, sky with distant birds, wildflowers in the foreground where appropriate), reverent and hopeful mood, gentle painted brushwork visible but not heavy, no photorealism but high level of painted detail, full-bleed cinematic frame with no decorative borders.
```

### Tradition-specific style additions

- **Buddhism (Theravāda focus)** — set in ancient India or Sri Lanka, monks in saffron robes, gentle tropical landscape (banyan and bodhi trees, lotus ponds, stupas in distance), warm sunlit palette filtered through leaves
- **Buddhism (Mahāyāna / Vajrayāna focus)** — Himalayan landscape with monasteries, monks in maroon robes, prayer flags fluttering, snow-capped peaks in distance, cool clear high-altitude light
- **Christianity (Western)** — first-century Galilee or Judea, period-accurate Jewish dress (linen tunics, head wraps, sandals), Sea of Galilee or rolling hills with olive trees in background, warm Mediterranean golden-hour light
- **Christianity (Eastern Orthodox)** — Byzantine Empire setting, more iconographic restraint in poses (frontal teaching positions, gentle gestures), gold-touched light without overt halos, monastic stone architecture
- **Islam** — Arabian, Persian, or Andalusian landscape with mosque architecture (domes, minarets, courtyards with fountains), worshippers in traditional dress, calligraphy on walls; **no figural depictions of the Prophet Muhammad, other prophets, or angels** — use scenes of pilgrims at the Kaaba, open Mushaf on a stand, hands raised in duʿāʾ, mosque interiors filled with worshippers, oil lamps, Qur'anic calligraphy
- **Hinduism** — lush Indian setting (Himalayan foothills, the Ganges, banyan trees, temple complexes, ashrams), sages and devotees in traditional Indian dress, warm sunlit golden palette, deities depicted with correct iconography for their specific identity (Vishnu's attributes vs Shiva's vs Devi's — not generic)
- **Judaism** — ancient Israel landscape (Jerusalem, Mount Sinai, Negev desert, Galilee), figures in period Jewish dress (tallit, tzitzit where appropriate), Torah scrolls, warm Mediterranean light. For Orthodox-leaning content, avoid depicting the divine Name or attempting to picture God
- **Sikhism** — Punjab landscape with golden mustard fields, gurdwaras (especially the Harmandir Sahib / Golden Temple), figures with dastars (turbans) and kirpans, devotees doing seva, warm sunlit palette
- **Taoism / Confucianism** — classical Chinese landscape (misty mountains, pine trees, scholars by streams, traditional courtyard architecture), sages in flowing robes, atmospheric mist and clouds, soft palette of greens / blues / earth
- **Jainism** — ancient Indian setting with Jain temples (Shikhar style), ascetics in white robes, pastoral peaceful surroundings, soft warm palette

### Scene specifics
After BASE STYLE + tradition layer, describe the concrete scene in ~25-40 words: subject, action, posture, key objects, mood. Stay concrete — avoid "spirituality" or "divinity" abstractions.

### Iconographic rules — honor these or practitioners lose trust

- **Islam** — never depict Muhammad, other prophets, or angels in figural form
- **Judaism (traditional)** — avoid graven-image depictions of God; names of God should not appear in disposable images
- **Buddhism** — follow traditional mudras when showing the Buddha or bodhisattvas
- **Hinduism** — match attributes to the specific deity (Vishnu vs Shiva); generic "Hindu god" reads as ignorant
- **Christianity (Eastern Orthodox)** — use icon conventions when showing Christ or saints

### Image size

kie.ai's `gpt-image-2-text-to-image` model accepts **aspect ratio strings**, not pixel dimensions. The default is `16:9` — fills the YouTube frame edge-to-edge with no pillarbox.

| `--size` | Aspect | When to use |
|---|---|---|
| **`16:9`** (default) | Landscape 16:9 | **Default for most scenes.** Native YouTube format — fills the frame, zero pillarbox. |
| `9:16` | Portrait 16:9 | YouTube Shorts vertical, or vertical subjects (standing deity/saint, minaret, stupa). |
| `4:3` | Landscape 4:3 | Slightly squarer composition with mild pillarbox in 16:9 final. |
| `3:4` | Portrait 4:3 | Vertical with slight pillarbox. |
| `3:2` | Landscape 3:2 | Classic photography aspect; ~150px pillarbox each side in final. |
| `2:3` | Portrait 2:3 | Tall portrait with pillarbox. |
| `1:1` | Square | Centripetal subjects: mandala, yantra, dharma wheel, gospel cover, calligraphic medallion. Half-frame visible in 16:9. |

Do **not** pass pixel dimensions like `1920x1080` or `1024x1024` — kie.ai rejects those with `code: 422, msg: 'size error'`.

The merge script reads each image's actual pixel dimensions via `ffprobe` and fits any aspect ratio to the 1920×1080 canvas, with blurred-darkened background fill for the sides when needed. Mixing aspect ratios within one video is fine.

---

## Step 6 — Show the script for review

Before any API calls, show the user the full script (all three sections — voice text, image prompts, timing table). Ask "Proceed with generation?" and wait for explicit confirmation. If the user wants script edits, apply them and re-show before continuing.

---

## Step 7 — Pick a voice (ElevenLabs)

Show 3-4 recommended voices for the tradition (see `references/voice-ids.md` for the full table). Default suggestions:

- **Universal contemplative** — `pNInz6obpgDQGcFmaJgB` (Adam): deep, calm, mature American male
- **Documentary / scholarly** — `onwK4e9ZLuTAKqWW03F9` (Daniel): BBC-style British, authoritative
- **Warm storyteller** — `JBFqnCBsd6RMkjVDRZzb` (George): warm British, good for Christianity
- **Deep narrator / gravitas** — `nPczCjzI2devNBz1zQrb` (Brian): deep American, slow, documentary
- **Soft contemplative** — `ErXwobaYiN019PkySvjV` (Antoni): warm, gentle, good for Buddhism / meditation content

Ask the user to pick by name or ID. They can also provide a custom voice ID (their own cloned voice).

Read `references/voice-ids.md` for the full library with tradition-pairing notes.

---

## Step 8 — Create folder + write inputs

Slug the title (lowercase, dashes). The full path:
```
<skill-root>/projects/<YYYY-MM-DD>_<slug>/
```

Inside, create:
```
<video-folder>/
├── script.md            ← THE source of truth: voice text with === between image segments
├── images/
│   ├── 01_<label>.txt   ← image prompt 1
│   ├── 02_<label>.txt   ← image prompt 2
│   └── ...
└── voice/               ← (created by generate_voice.py)
```

### `script.md` format

Pure spoken text, with a single `===` line between each image segment. The number of `===`-separated sections must equal the number of images.

```
For thirty minutes, Jesus had been on the cross. Then a strange darkness covered the land...

===

The cry is preserved in two of the four Gospels. Mark fifteen thirty-four, Matthew twenty-seven forty-six...

===

This is where the tradition divides. Three readings have been carried by the church...

===

What does this change for the believer this week? Not theology in the abstract...
```

The `===` markers are stripped before sending to ElevenLabs, and used by `merge_video.py` to figure out which paragraph belongs to which image.

### Image prompt files

One `.txt` per image, named `01_<label>.txt`, `02_<label>.txt`, etc. Each contains the full prompt (BASE STYLE + tradition layer + scene). The image generation produces matching `.png` files in the same folder.

---

## Step 9 — Generate assets (run in parallel)

### Voice

```bash
python scripts/generate_voice.py \
  --text-file <video-folder>/script.md \
  --voice-id <chosen_voice_id> \
  --output <video-folder>/voice/voice.mp3 \
  > <video-folder>/voice/voice_metadata.json
```

The script auto-strips `===` markers. Default voice settings (`stability=0.55, similarity_boost=0.75, style=0.35, speed=0.92`) are tuned for contemplative narration — override only if user asks.

### Images

For each prompt file, in parallel (multiple Bash tool calls in one message):

```bash
python scripts/generate_image.py \
  --prompt-file <video-folder>/images/01_<label>.txt \
  --output <video-folder>/images/01_<label>.png \
  --size 16:9 \
  > <video-folder>/images/01_<label>.metadata.json
```

Each call polls kie.ai for 30-90 seconds.

---

## Step 10 — Assemble into final.mp4

```bash
python scripts/merge_video.py --video-folder <video-folder>
```

This single command:
1. Splits `script.md` by `===` into N sections, counts words per section
2. Reads `voice.mp3` duration via `ffprobe`
3. Computes each image's display window as `(words_in_section / total_words) × audio_duration`
4. Renders each PNG into a 1920×1080 clip (centered with blurred fill if source isn't 16:9)
5. Chains clips with 0.6s crossfade, muxes in voice audio
6. Outputs `final.mp4`

PNGs in `images/` are matched to sections in **sorted filename order** — so consistent naming (`01_`, `02_`, ...) is required.

No manual timing file. No estimate-then-adjust step. Timing is calculated once, from actual audio length, after voice generation.

---

## Step 11 — Report to the user

When done, report:
- Absolute path to the video folder
- File listing (script.md, voice/voice.mp3, images/*.png, final.mp4)
- Any failures and how to retry

Common retries:
- **Failed image** — rerun `generate_image.py` with the same prompt file; overwrite the PNG; rerun `merge_video.py`
- **Voice tone wrong** — rerun `generate_voice.py` with a different voice ID; rerun `merge_video.py`
- **Image-to-speech sync feels off** — adjust where you place the `===` markers in `script.md` (move them earlier/later relative to a paragraph), then rerun `merge_video.py`. No need to regenerate voice or images.

---

## Error handling

- **Missing API key** — script exits with a clear message naming which key is missing
- **API error (rate limit, invalid key, etc.)** — script prints the API error verbatim to stderr; relay it to the user and pause the pipeline
- **kie.ai task timeout** — `generate_image.py` polls up to 5 minutes by default; if a task hangs longer, kill it and retry
- **Partial pipeline failure** — never delete the output folder. If 1 of 5 images failed, the user keeps the other 4 and reruns just the failed one

---

## Common script-writing mistakes to avoid

- **Skipping Layer 2** — Layer 1 + Layer 3 alone collapses into either dry exegesis or feel-good devotion. Layer 2 (theological / commentator) is what practitioners came for.
- **Mixing tradition aesthetics in images** — never put Christian motifs in a Buddhism video or vice versa. The tradition-specific scene details (clothing, architecture, landscape) are a respect signal that practitioners notice immediately.
- **More than 8 images** — pacing collapses into slideshow.
- **Generic image prompts** — "a peaceful religious scene" is not a prompt. Name specific iconography, posture, objects, palette.
- **Academic register in voice script** — depth ≠ jargon density. Read each paragraph aloud; if it sounds like a journal article, simplify sentence structure while keeping specialist terms.
- **Citing sources you're not sure about** — if uncertain about a commentator's view or verse number, say "the tradition broadly holds..." or omit it. Practitioners check.

---

## Files in this skill

- `SKILL.md` — this file
- `scripts/generate_voice.py` — ElevenLabs voice generation (input: text file + voice ID; output: MP3)
- `scripts/generate_image.py` — kie.ai `gpt-image-2-text-to-image` generation (input: prompt file; output: PNG)
- `scripts/merge_video.py` — FFmpeg assembler (input: video folder; output: final.mp4)
- `requirements.txt` — Python dependencies
- `.env.example` — template for API keys (copy to `.env` and fill in)
- `.gitignore` — keeps `.env` and `projects/` out of version control
- `projects/` — **generated video output goes here**, one subfolder per video (`<YYYY-MM-DD>_<slug>/`). Created on first run.
- `references/voice-ids.md` — recommended ElevenLabs voices with tradition-pairing notes
- `references/kie-api-setup.md` — kie.ai API endpoint details + adjustments
- `references/ffmpeg-setup.md` — installing FFmpeg, what merge_video.py does, common errors
