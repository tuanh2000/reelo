"""ElevenLabs voice client — wraps the skill ``generate_voice.py`` (Module 3).

Skill-wrapper family: shells out to the standalone ElevenLabs script via
:func:`clients.subprocess_util.run_skill_script`, injecting the user's BYOK key
into the subprocess env (``ELEVENLABS_API_KEY``) just-in-time. The script reads
a ``--text-file``, so a direct ``VoiceRequest.text`` is written to a temp file
first.

The script's stdout JSON (``output_path``, ``character_count``, …) is mapped to
:class:`VoiceResult`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from clients.base import (
    AIClient,
    CallContext,
    InvalidKeyError,
    ProviderUnavailableError,
    Task,
    VoiceRequest,
    VoiceResult,
)
from clients.subprocess_util import run_skill_script
from usage import compute_cost

_SCRIPT = "generate_voice.py"


class SkillVoiceClient(AIClient):
    """ElevenLabs TTS via the skill subprocess."""

    requires_key = True

    def _env_var(self) -> str:
        return self.config.auth.env or "ELEVENLABS_API_KEY"

    def _key_ref(self) -> str:
        return self.config.auth.key_ref or "elevenlabs"

    def _key_env(self, ctx: CallContext) -> dict[str, str]:
        env = ctx.keys.as_env(ctx.user_id, {self._env_var(): self._key_ref()})
        if self._env_var() not in env:
            raise InvalidKeyError(f"No ElevenLabs key for user {ctx.user_id}")
        return env

    # ---- validate ----------------------------------------------------------
    async def validate_key(self, ctx: CallContext) -> bool:
        """Validate the key with a lightweight ElevenLabs API call (no skill script).

        Hits ``GET /v1/user`` directly so we do not spend TTS characters.
        """
        import httpx

        key = ctx.keys.get(ctx.user_id, self._key_ref())
        if not key:
            raise InvalidKeyError(f"No ElevenLabs key for user {ctx.user_id}")
        try:
            async with httpx.AsyncClient(timeout=30) as http:
                r = await http.get(
                    "https://api.elevenlabs.io/v1/user", headers={"xi-api-key": key}
                )
            if r.status_code in (401, 403):
                raise InvalidKeyError("ElevenLabs rejected the key")
            if r.status_code >= 400:
                raise ProviderUnavailableError(f"ElevenLabs HTTP {r.status_code}")
            return True
        except InvalidKeyError:
            raise
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(str(exc)) from exc

    # ---- generate-voice ----------------------------------------------------
    async def generate_voice(
        self, req: VoiceRequest, out_path: Path, ctx: CallContext
    ) -> VoiceResult:
        env = self._key_env(ctx)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_text: Path | None = None
        if req.text_file is not None:
            text_file = Path(req.text_file)
        elif req.text is not None:
            fd = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            )
            fd.write(req.text)
            fd.close()
            text_file = tmp_text = Path(fd.name)
        else:
            raise ProviderUnavailableError("VoiceRequest needs text or text_file")

        settings = req.settings or {}
        args = [
            "--text-file", str(text_file),
            "--voice-id", req.voice_id,
            "--output", str(out_path),
        ]
        block = self.config.tasks.get(Task.GENERATE_VOICE.value, {}) or {}
        model = settings.get("model") or (block.get("models") or [None])[0]
        if model:
            args += ["--model", model]
        for cli, key in (
            ("--stability", "stability"),
            ("--similarity-boost", "similarity_boost"),
            ("--style", "style"),
            ("--speed", "speed"),
        ):
            if key in settings:
                args += [cli, str(settings[key])]

        try:
            result = await run_skill_script(_SCRIPT, args, env=env)
        finally:
            if tmp_text is not None:
                tmp_text.unlink(missing_ok=True)

        chars = int(result.get("character_count", 0))
        cost = compute_cost(Task.GENERATE_VOICE.value, float(chars), self.config.pricing)
        ctx.usage.record(ctx.user_id, self.provider_id, Task.GENERATE_VOICE.value, float(chars), cost)
        return VoiceResult(
            out_path=Path(result.get("output_path", out_path)),
            duration_s=None,
            chars=chars,
            raw=result,
        )


__all__ = ["SkillVoiceClient"]
