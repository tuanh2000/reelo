"""Gemini client over the Google AI Studio official API (Module 3, M3-3).

Uses the ``google-genai`` SDK. Replaces the dropped gemini-web2api: this is the
official API so it has real JSON mode (``response_schema`` /
``response_mime_type``) and a real ``system_instruction`` — no sentinel parsing.

Capabilities:
- WRITE_SCRIPT — structured output + system prompt, token usage mapped.
- GENERATE_IMAGE — Imagen via ``generate_images`` (optional; YAML-gated).

BYOK: the user's ``google_aistudio`` key is read from ``ctx.keys`` per call; the
client itself is stateless and constructs a short-lived SDK client per request.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from clients.base import (
    AIClient,
    CallContext,
    ImageRequest,
    ImageResult,
    InvalidKeyError,
    ProviderUnavailableError,
    ScriptRequest,
    ScriptResult,
    Task,
)
from usage import compute_cost


class GeminiClient(AIClient):
    """Google AI Studio (Gemini + Imagen)."""

    requires_key = True

    def _api_key(self, ctx: CallContext) -> str:
        key_ref = self.config.auth.key_ref or "google_aistudio"
        key = ctx.keys.get(ctx.user_id, key_ref)
        if not key:
            raise InvalidKeyError(f"No Google AI Studio key for user {ctx.user_id}")
        return key

    def _client(self, ctx: CallContext) -> Any:
        from google import genai

        return genai.Client(api_key=self._api_key(ctx))

    def _default_model(self, task: Task) -> str | None:
        block = self.config.tasks.get(task.value, {}) or {}
        return block.get("default_model") or (block.get("models") or [None])[0]

    # ---- validate ----------------------------------------------------------
    async def validate_key(self, ctx: CallContext) -> bool:
        """Cheap test call: list models (no generation cost)."""
        from google.genai import errors as genai_errors

        try:
            client = self._client(ctx)
            # list() returns a pager; pull a single page lazily.
            it = client.models.list()
            next(iter(it), None)
            return True
        except genai_errors.ClientError as exc:  # 4xx incl. 401/403
            if getattr(exc, "code", None) in (401, 403):
                raise InvalidKeyError(str(exc)) from exc
            raise ProviderUnavailableError(str(exc)) from exc
        except InvalidKeyError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(str(exc)) from exc

    # ---- write-script ------------------------------------------------------
    async def write_script(self, req: ScriptRequest, ctx: CallContext) -> ScriptResult:
        from google.genai import errors as genai_errors
        from google.genai import types

        client = self._client(ctx)
        model = req.model or self._default_model(Task.WRITE_SCRIPT) or "gemini-2.0-flash"

        contents = _messages_to_gemini(req.messages, types)

        config_kwargs: dict[str, Any] = {}
        if req.system:
            config_kwargs["system_instruction"] = req.system
        if req.temperature is not None:
            config_kwargs["temperature"] = req.temperature
        if req.json_schema:
            config_kwargs["response_mime_type"] = "application/json"
            config_kwargs["response_schema"] = req.json_schema
        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        try:
            resp = await client.aio.models.generate_content(
                model=model, contents=contents, config=config
            )
        except genai_errors.ClientError as exc:
            if getattr(exc, "code", None) in (401, 403):
                raise InvalidKeyError(str(exc)) from exc
            raise ProviderUnavailableError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(str(exc)) from exc

        text = resp.text or ""
        usage = _map_gemini_usage(resp)
        cost = compute_cost(
            Task.WRITE_SCRIPT.value, float(usage.get("total_tokens", 0)), self.config.pricing
        )
        ctx.usage.record(
            ctx.user_id, self.provider_id, Task.WRITE_SCRIPT.value,
            float(usage.get("total_tokens", 0)), cost,
        )
        return ScriptResult(text=text, model=model, usage=usage, raw=None)

    # ---- generate-image (Imagen) ------------------------------------------
    async def generate_image(
        self, req: ImageRequest, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        from google.genai import errors as genai_errors
        from google.genai import types

        if Task.GENERATE_IMAGE not in self.capabilities:
            return await super().generate_image(req, out_path, ctx)

        client = self._client(ctx)
        model = req.model if getattr(req, "model", None) else None
        model = model or self._default_model(Task.GENERATE_IMAGE) or "imagen-3.0-generate-002"
        prompt = _resolve_prompt(req)

        try:
            resp = await client.aio.models.generate_images(
                model=model,
                prompt=prompt,
                config=types.GenerateImagesConfig(
                    number_of_images=1, aspect_ratio=req.size
                ),
            )
        except genai_errors.ClientError as exc:
            if getattr(exc, "code", None) in (401, 403):
                raise InvalidKeyError(str(exc)) from exc
            raise ProviderUnavailableError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(str(exc)) from exc

        images = getattr(resp, "generated_images", None) or []
        if not images:
            raise ProviderUnavailableError("Imagen returned no images")
        image_bytes = images[0].image.image_bytes
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(image_bytes)

        cost = compute_cost(Task.GENERATE_IMAGE.value, 1.0, self.config.pricing)
        ctx.usage.record(ctx.user_id, self.provider_id, Task.GENERATE_IMAGE.value, 1.0, cost)
        return ImageResult(out_path=out_path, count=1, raw=None)


def _messages_to_gemini(messages: list[dict[str, Any]], types: Any) -> list[Any]:
    """Map ``[{role, content}]`` to google-genai ``Content`` list.

    Roles: ``assistant``/``ai`` -> ``model``; everything else -> ``user``.
    """
    out: list[Any] = []
    for m in messages:
        role = m.get("role", "user")
        g_role = "model" if role in ("assistant", "ai", "model") else "user"
        out.append(
            types.Content(role=g_role, parts=[types.Part.from_text(text=str(m.get("content", "")))])
        )
    return out


def _map_gemini_usage(resp: Any) -> dict[str, Any]:
    """Map ``usage_metadata`` to the common ``{prompt,completion,total}`` shape."""
    um = getattr(resp, "usage_metadata", None)
    if um is None:
        return {}
    prompt = getattr(um, "prompt_token_count", 0) or 0
    completion = getattr(um, "candidates_token_count", 0) or 0
    total = getattr(um, "total_token_count", 0) or (prompt + completion)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": total,
    }


def _resolve_prompt(req: ImageRequest) -> str:
    if req.prompt:
        return req.prompt
    if req.prompt_file:
        return Path(req.prompt_file).read_text(encoding="utf-8").strip()
    raise ProviderUnavailableError("ImageRequest needs prompt or prompt_file")


__all__ = ["GeminiClient"]
