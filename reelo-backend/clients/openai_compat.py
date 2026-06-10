"""OpenAI-compatible chat client (Module 3).

One class serves every provider that speaks the OpenAI chat-completions API:
ChatGPT (default endpoint) and DeepSeek (``endpoint`` from YAML). Adding another
OpenAI-compatible provider is zero code — just a YAML block pointing here.

Capabilities:
- WRITE_SCRIPT — structured output via ``response_format`` (json_schema when the
  request carries one, else json_object), real system prompt, token usage mapped.
- GENERATE_IMAGE — only when the provider's YAML ``tasks`` declares it (ChatGPT
  ``gpt-image-1`` / DALL·E). DeepSeek does not, so it stays unsupported there.

BYOK: the per-provider key (``openai`` / ``deepseek``) is read from ``ctx.keys``
each call. Stateless; a short-lived ``AsyncOpenAI`` client is built per request.
"""

from __future__ import annotations

import base64
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


class OpenAIStyleClient(AIClient):
    """Client for any OpenAI chat-completions-compatible provider."""

    requires_key = True

    def _api_key(self, ctx: CallContext) -> str:
        key_ref = self.config.auth.key_ref or "openai"
        key = ctx.keys.get(ctx.user_id, key_ref)
        if not key:
            raise InvalidKeyError(f"No {self.provider_id} key for user {ctx.user_id}")
        return key

    def _client(self, ctx: CallContext) -> Any:
        from openai import AsyncOpenAI

        kwargs: dict[str, Any] = {"api_key": self._api_key(ctx)}
        if self.config.endpoint:
            kwargs["base_url"] = self.config.endpoint
        return AsyncOpenAI(**kwargs)

    def _default_model(self, task: Task) -> str | None:
        block = self.config.tasks.get(task.value, {}) or {}
        return block.get("default_model") or (block.get("models") or [None])[0]

    # ---- validate ----------------------------------------------------------
    async def validate_key(self, ctx: CallContext) -> bool:
        """Cheap test call: list models."""
        from openai import APIStatusError, AuthenticationError

        client = self._client(ctx)
        try:
            await client.models.list()
            return True
        except AuthenticationError as exc:
            raise InvalidKeyError(str(exc)) from exc
        except APIStatusError as exc:
            if exc.status_code in (401, 403):
                raise InvalidKeyError(str(exc)) from exc
            raise ProviderUnavailableError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(str(exc)) from exc

    # ---- write-script ------------------------------------------------------
    async def write_script(self, req: ScriptRequest, ctx: CallContext) -> ScriptResult:
        from openai import APIStatusError, AuthenticationError

        client = self._client(ctx)
        model = req.model or self._default_model(Task.WRITE_SCRIPT) or "gpt-4o"

        messages: list[dict[str, Any]] = []
        if req.system:
            messages.append({"role": "system", "content": req.system})
        for m in req.messages:
            role = m.get("role", "user")
            role = "assistant" if role in ("assistant", "ai", "model") else role
            messages.append({"role": role, "content": str(m.get("content", ""))})

        kwargs: dict[str, Any] = {"model": model, "messages": messages}
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "reelo_script",
                    "schema": req.json_schema,
                    "strict": False,
                },
            }

        try:
            resp = await client.chat.completions.create(**kwargs)
        except AuthenticationError as exc:
            raise InvalidKeyError(str(exc)) from exc
        except APIStatusError as exc:
            if exc.status_code in (401, 403):
                raise InvalidKeyError(str(exc)) from exc
            raise ProviderUnavailableError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(str(exc)) from exc

        text = resp.choices[0].message.content or ""
        usage = _map_openai_usage(resp)
        cost = compute_cost(
            Task.WRITE_SCRIPT.value, float(usage.get("total_tokens", 0)), self.config.pricing
        )
        ctx.usage.record(
            ctx.user_id, self.provider_id, Task.WRITE_SCRIPT.value,
            float(usage.get("total_tokens", 0)), cost,
        )
        return ScriptResult(text=text, model=model, usage=usage, raw=None)

    # ---- generate-image ----------------------------------------------------
    async def generate_image(
        self, req: ImageRequest, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        from openai import APIStatusError, AuthenticationError

        if Task.GENERATE_IMAGE not in self.capabilities:
            return await super().generate_image(req, out_path, ctx)

        client = self._client(ctx)
        model = self._default_model(Task.GENERATE_IMAGE) or "gpt-image-1"
        prompt = _resolve_prompt(req)
        size = _normalize_size(req.size, self.config.tasks.get(Task.GENERATE_IMAGE.value, {}))

        try:
            resp = await client.images.generate(
                model=model, prompt=prompt, size=size, n=1
            )
        except AuthenticationError as exc:
            raise InvalidKeyError(str(exc)) from exc
        except APIStatusError as exc:
            if exc.status_code in (401, 403):
                raise InvalidKeyError(str(exc)) from exc
            raise ProviderUnavailableError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(str(exc)) from exc

        datum = resp.data[0]
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        if getattr(datum, "b64_json", None):
            out_path.write_bytes(base64.b64decode(datum.b64_json))
        elif getattr(datum, "url", None):
            await _download(datum.url, out_path)
        else:
            raise ProviderUnavailableError("Image response had neither b64_json nor url")

        cost = compute_cost(Task.GENERATE_IMAGE.value, 1.0, self.config.pricing)
        ctx.usage.record(ctx.user_id, self.provider_id, Task.GENERATE_IMAGE.value, 1.0, cost)
        return ImageResult(out_path=out_path, count=1, raw=None)


def _map_openai_usage(resp: Any) -> dict[str, Any]:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    prompt = getattr(u, "prompt_tokens", 0) or 0
    completion = getattr(u, "completion_tokens", 0) or 0
    total = getattr(u, "total_tokens", 0) or (prompt + completion)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total}


def _normalize_size(size: str, image_block: dict[str, Any]) -> str:
    """OpenAI images want pixel sizes (1024x1024). Map common aspect ratios."""
    if "x" in size:
        return size
    sizes = image_block.get("sizes") or []
    ratio_map = {"1:1": "1024x1024", "16:9": "1792x1024", "9:16": "1024x1792"}
    mapped = ratio_map.get(size)
    if mapped and (not sizes or mapped in sizes):
        return mapped
    if sizes:
        return sizes[0]
    return "1024x1024"


def _resolve_prompt(req: ImageRequest) -> str:
    if req.prompt:
        return req.prompt
    if req.prompt_file:
        return Path(req.prompt_file).read_text(encoding="utf-8").strip()
    raise ProviderUnavailableError("ImageRequest needs prompt or prompt_file")


async def _download(url: str, out_path: Path) -> None:
    import httpx

    async with httpx.AsyncClient(timeout=120) as http:
        r = await http.get(url)
        r.raise_for_status()
        out_path.write_bytes(r.content)


__all__ = ["OpenAIStyleClient"]
