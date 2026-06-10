# ElevenLabs Voice IDs for Religious YouTube Content

All voice IDs below are from ElevenLabs' default library (free to use with any paid plan). For best results pair them with the contemplative defaults: `stability=0.55, similarity_boost=0.75, style=0.35, speed=0.92`, model `eleven_multilingual_v2`.

## Top picks (recommend these first)

| Voice ID | Name | Accent / Tone | Best for |
|---|---|---|---|
| `pNInz6obpgDQGcFmaJgB` | Adam | American, deep, mature, calm | **Default for any tradition.** Versatile contemplative narrator. |
| `nPczCjzI2devNBz1zQrb` | Brian | American, deep, slow, narrator | Long-form documentary feel. Best for gravitas-heavy topics (death, suffering, judgement). |
| `onwK4e9ZLuTAKqWW03F9` | Daniel | British, authoritative, BBC-style | Scholarly explainer videos. Strong for textual / historical content. |
| `JBFqnCBsd6RMkjVDRZzb` | George | British, warm, storyteller | Warm tone for Christianity, narrative passages, parables. |
| `ErXwobaYiN019PkySvjV` | Antoni | American, warm, gentle | Soft contemplative — Buddhist, meditative, mystical content. |

## By tradition (defaults if user has no preference)

| Tradition | Default voice | Alternative |
|---|---|---|
| Buddhism (Theravāda, Zen) | Antoni (soft, contemplative) | Adam |
| Buddhism (Vajrayāna, Tibetan) | Adam (steady, present) | Brian |
| Christianity (Catholic, Orthodox) | George (warm British) | Daniel |
| Christianity (Protestant, Evangelical) | Adam (American, clear) | Brian |
| Islam (Sunni, Shia) | Daniel (BBC authority) | Brian |
| Islam (Sufi / mystical) | Antoni (warm, intimate) | George |
| Hinduism | Daniel (scholarly) | Adam |
| Judaism | Daniel (scholarly) | George |
| Sikhism | Adam | George |
| Taoism / Confucianism | Antoni (soft) | Adam |
| Jainism | Antoni | Adam |

## Other voices worth knowing

| Voice ID | Name | Notes |
|---|---|---|
| `IKne3meq5aSn9XLyUdCD` | Charlie | Younger Australian male, conversational. Use for accessible / explainer-style content aimed at younger practitioners. |
| `pqHfZKP75CvOlQylNhV4` | Bill | Older American male, gravitas. Good for historical / death / eschatology content. |
| `XB0fDUnXU5powFXDhCwa` | Charlotte | Female, warm Swedish-English. Use sparingly — most religion channels default to male narration, but female voices work well for feminist-theology or female-saint content (Hildegard, Teresa of Ávila, Therīgāthā). |
| `cgSgspJ2msm6clMCkdW9` | Jessica | Female, warm American, contemplative. Alternative female option. |

## Cloned voices

If the user has cloned their own voice on ElevenLabs (Pro tier+), they can paste their custom voice ID directly. Cloned voices skip the library entirely.

## How to verify a voice exists

```bash
curl -X GET "https://api.elevenlabs.io/v1/voices/<VOICE_ID>" \
     -H "xi-api-key: $ELEVENLABS_API_KEY"
```

A 200 response means the voice is available to your account. A 404 means the ID is wrong or not in your library.

## Voice settings cheat sheet

Default for contemplative religious narration:
- `stability=0.55` — stable enough to sound serious, loose enough to keep emotion
- `similarity_boost=0.75` — preserves the voice's character
- `style=0.35` — modest stylization; higher numbers can sound theatrical
- `speed=0.92` — slightly slower than natural for gravitas
- `use_speaker_boost=true` — improves clarity for dense passages

For very dense theological passages (Layer 2 commentator analysis), drop speed to `0.88`. For lighter narrative openings, raise to `0.96`.
