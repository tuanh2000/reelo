# kie.ai API Setup Notes

[kie.ai](https://kie.ai) is a third-party aggregator that exposes many AI models behind a unified API. This skill uses kie.ai's `gpt-image-2-text-to-image` model for image generation.

## Getting an API key

1. Sign up at [kie.ai](https://kie.ai)
2. Top up credit (they're prepaid)
3. Dashboard → API Keys → create a new key
4. Paste into `.env` as `KIE_API_KEY=`

## The endpoints this skill uses

`scripts/generate_image.py` calls the unified jobs API:

- **Submit task:** `POST https://api.kie.ai/api/v1/jobs/createTask`
- **Poll status:** `GET https://api.kie.ai/api/v1/jobs/recordInfo?taskId=<id>`

Authentication: `Authorization: Bearer <KIE_API_KEY>`.

## Request body

```json
{
  "model": "gpt-image-2-text-to-image",
  "input": {
    "prompt": "your full prompt here",
    "size": "16:9"
  }
}
```

**Important:** `size` is an aspect ratio string, **not** pixel dimensions. Valid values:

- `1:1` — square
- `4:3` / `3:4` — classic landscape / portrait
- `3:2` / `2:3` — photography aspect
- `16:9` / `9:16` — YouTube / vertical

Passing `1920x1080`, `1024x1024`, etc. returns `code: 422, msg: "size error"`.

## Poll response shape

```json
{
  "code": 200,
  "msg": "success",
  "data": {
    "taskId": "abc123...",
    "model": "gpt-image-2-text-to-image",
    "state": "generating",      // or "success" / "failed"
    "param": "...",              // your input JSON-stringified
    "resultJson": "",            // contains the image URL when state == "success"
    "failCode": null,
    "failMsg": null,
    "costTime": null,
    "completeTime": null,
    "createTime": 1779114168227,
    "creditsConsumed": 6.0
  }
}
```

State flow: `generating` → `success` (or `failed`). When successful, `resultJson` is a JSON-encoded string. `generate_image.py` parses it and walks common key names (`resultUrls`, `urls`, `images`, `output`, etc.) to find the image URL, then downloads.

Generated image URLs typically live at `tempfile.aiquickdraw.com` and are valid for ~24 hours — the script downloads immediately.

## Pricing

The `gpt-image-2-text-to-image` model is roughly **6 credits per image** (varies by aspect ratio). Check the current kie.ai pricing page for exact USD-per-credit on your plan.

## Common errors

| Error | Likely cause |
|---|---|
| `code: 401 / Unauthorized` | Bad or missing API key |
| `code: 402 / Insufficient credit` | Top up credit in dashboard |
| `code: 422 / size error` | You passed pixel dimensions instead of a ratio string — use `16:9`, `1:1`, etc. |
| `code: 429 / Rate limit` | Too many concurrent requests — slow down or upgrade plan |
| `state: failed` (content) | Prompt rejected by content filter. Soften wording — rare for religious manuscript content. |
| Task hangs > 5 min | Service slow — pass `--max-wait 600` to extend, or kill and retry |

## Why this skill uses jobs/createTask instead of gpt4o-image/generate

kie.ai has two image endpoints with different capabilities:

- `/api/v1/gpt4o-image/generate` — older endpoint, only supports 3 sizes (`1:1`, `3:2`, `2:3`), no 16:9
- `/api/v1/jobs/createTask` with model `gpt-image-2-text-to-image` — newer endpoint, supports **all common aspect ratios including 16:9** — preferred for YouTube content

If the user has an older kie.ai plan that lacks the jobs endpoint, edit `scripts/generate_image.py` and change `KIE_BASE`, the endpoint paths, and the request body shape back to the gpt4o-image format. The poll logic handles both response shapes.

## Alternative providers

If kie.ai is unavailable, the same pipeline works with minor edits:

- **OpenAI direct** (`POST https://api.openai.com/v1/images/generations`, model `gpt-image-1`) — synchronous, no polling, ~2x the cost, only 3 sizes (1024×1024 / 1024×1536 / 1536×1024)
- **Replicate** (`POST https://api.replicate.com/v1/predictions`, model `black-forest-labs/flux-1.1-pro`) — different prompt style, cheaper, excellent for illuminated-manuscript aesthetic, supports any aspect ratio
