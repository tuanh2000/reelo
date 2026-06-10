"""kie.ai image client — wraps the skill ``generate_image.py`` (Module 3).

Skill-wrapper family: shells out to the standalone kie.ai script via
:func:`clients.subprocess_util.run_skill_script`, injecting the user's BYOK key
into the subprocess env (``KIE_API_KEY``) just-in-time. The script reads a
``--prompt-file``, so a direct ``ImageRequest.prompt`` is written to a temp file
first. ``--size`` is the aspect-ratio string from ``ImageRequest.size``.

The script's stdout JSON (``output_path``, ``size``, ``source_url``, …) maps to
:class:`ImageResult`.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from clients.base import (
    AIClient,
    CallContext,
    ImageRequest,
    ImageResult,
    InvalidKeyError,
    ProviderUnavailableError,
    Task,
)
from clients.subprocess_util import run_skill_script
from usage import compute_cost

_SCRIPT = "generate_image.py"
# Mirrors VALID_SIZES in generate_image.py (kie gpt-image-2 ratio strings).
_VALID_SIZES = {"1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"}


class SkillImageClient(AIClient):
    """kie.ai text-to-image via the skill subprocess."""

    requires_key = True

    def _env_var(self) -> str:
        return self.config.auth.env or "KIE_API_KEY"

    def _key_ref(self) -> str:
        return self.config.auth.key_ref or "kie"

    def _key_env(self, ctx: CallContext) -> dict[str, str]:
        env = ctx.keys.as_env(ctx.user_id, {self._env_var(): self._key_ref()})
        if self._env_var() not in env:
            raise InvalidKeyError(f"No kie.ai key for user {ctx.user_id}")
        return env

    def _resolve_size(self, size: str) -> str:
        if size in _VALID_SIZES:
            return size
        block = self.config.tasks.get(Task.GENERATE_IMAGE.value, {}) or {}
        return block.get("default_size", "16:9")

    # ---- validate ----------------------------------------------------------
    async def validate_key(self, ctx: CallContext) -> bool:
        """Presence + format check.

        kie.ai has no documented cheap auth-only endpoint and the skill script
        only exercises generation (which would spend credits). v1 validation is
        non-empty key presence; a wrong key surfaces as :class:`InvalidKeyError`
        on the first real generation.
        """
        key = ctx.keys.get(ctx.user_id, self._key_ref())
        if not key:
            raise InvalidKeyError(f"No kie.ai key for user {ctx.user_id}")
        return True

    # ---- generate-image ----------------------------------------------------
    async def generate_image(
        self, req: ImageRequest, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        env = self._key_env(ctx)
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        tmp_prompt: Path | None = None
        if req.prompt_file is not None:
            prompt_file = Path(req.prompt_file)
        elif req.prompt is not None:
            fd = tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False, encoding="utf-8"
            )
            fd.write(req.prompt)
            fd.close()
            prompt_file = tmp_prompt = Path(fd.name)
        else:
            raise ProviderUnavailableError("ImageRequest needs prompt or prompt_file")

        args = [
            "--prompt-file", str(prompt_file),
            "--output", str(out_path),
            "--size", self._resolve_size(req.size),
        ]
        try:
            result = await run_skill_script(_SCRIPT, args, env=env)
        finally:
            if tmp_prompt is not None:
                tmp_prompt.unlink(missing_ok=True)

        cost = compute_cost(Task.GENERATE_IMAGE.value, 1.0, self.config.pricing)
        ctx.usage.record(ctx.user_id, self.provider_id, Task.GENERATE_IMAGE.value, 1.0, cost)
        return ImageResult(
            out_path=Path(result.get("output_path", out_path)), count=1, raw=result
        )


__all__ = ["SkillImageClient"]
