"""Edge-TTS voice client — pure-Python, KEYLESS (Module 3).

Microsoft Edge's free neural TTS via the ``edge-tts`` package. The only keyless
provider (``requires_key = False``) so the fully-free path needs no API key
(M3-2). ``voices_by_language`` in YAML maps a language code to a default voice;
a per-request ``voice_id`` overrides it.

No usage cost (free), but we still record units (characters) for the dashboard.
Note: keyless + shared server IP can hit Microsoft rate-limits at scale
(integration.md risk #11) — surfaced as :class:`ProviderUnavailableError`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clients.base import (
    AIClient,
    CallContext,
    ProviderUnavailableError,
    Task,
    VoiceRequest,
    VoiceResult,
)
from usage import compute_cost


class EdgeTTSClient(AIClient):
    """Free keyless TTS via edge-tts."""

    cost_tier = "free"
    requires_key = False

    def voices_by_language(self) -> dict[str, str]:
        block = self.config.tasks.get(Task.GENERATE_VOICE.value, {}) or {}
        return block.get("voices_by_language", {}) or {}

    def default_voice_for(self, language: str | None) -> str | None:
        vbl = self.voices_by_language()
        if language and language in vbl:
            return vbl[language]
        # first configured voice as a last resort
        return next(iter(vbl.values()), None)

    # keyless -> always available; default is_available already returns True
    async def is_available(self, ctx: CallContext) -> bool:
        return True

    async def validate_key(self, ctx: CallContext) -> bool:
        """Keyless: nothing to validate."""
        return True

    async def generate_voice(
        self, req: VoiceRequest, out_path: Path, ctx: CallContext
    ) -> VoiceResult:
        import edge_tts

        if req.text is not None:
            text = req.text
        elif req.text_file is not None:
            text = Path(req.text_file).read_text(encoding="utf-8")
        else:
            raise ProviderUnavailableError("VoiceRequest needs text or text_file")
        text = _strip_image_markers(text)
        if not text.strip():
            raise ProviderUnavailableError("Empty voice text")

        voice = req.voice_id or self.default_voice_for(
            (req.settings or {}).get("language")
        )
        if not voice:
            raise ProviderUnavailableError("No edge-tts voice resolved")

        kwargs = _edge_settings(req.settings or {})
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            comm = edge_tts.Communicate(text, voice, **kwargs)
            await comm.save(str(out_path))
        except Exception as exc:  # noqa: BLE001 — network / rate-limit
            raise ProviderUnavailableError(f"edge-tts failed: {exc}") from exc

        chars = len(text)
        cost = compute_cost(Task.GENERATE_VOICE.value, float(chars), self.config.pricing)
        ctx.usage.record(ctx.user_id, self.provider_id, Task.GENERATE_VOICE.value, float(chars), cost)
        return VoiceResult(out_path=out_path, duration_s=None, chars=chars, raw=None)


def _strip_image_markers(text: str) -> str:
    """Drop lone ``===`` image-break lines so the same script feeds voice + image."""
    lines = [ln for ln in text.split("\n") if ln.strip() != "==="]
    return "\n".join(lines).strip()


def _edge_settings(settings: dict[str, Any]) -> dict[str, str]:
    """Map optional ``rate``/``volume``/``pitch`` settings to edge-tts kwargs."""
    out: dict[str, str] = {}
    for k in ("rate", "volume", "pitch"):
        if k in settings and settings[k] is not None:
            out[k] = str(settings[k])
    return out


__all__ = ["EdgeTTSClient"]
