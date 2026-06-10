"""Claude client over the Anthropic Messages API (Module 3).

Capability: WRITE_SCRIPT with a real ``system`` prompt and structured output.
Anthropic has no ``response_format``; the idiomatic way to force JSON to a schema
is a single forced tool call whose ``input_schema`` is the request's
``json_schema``. We then return the tool input serialized as JSON text so Module
1's parse path is identical across providers. Without a schema we fall back to
plain text from the message content.

BYOK: the user's ``anthropic`` key is read from ``ctx.keys`` per call.
"""

from __future__ import annotations

import json
from typing import Any

from clients.base import (
    AIClient,
    CallContext,
    InvalidKeyError,
    ProviderUnavailableError,
    ScriptRequest,
    ScriptResult,
    Task,
)
from usage import compute_cost

_TOOL_NAME = "emit_script"


class ClaudeClient(AIClient):
    """Anthropic Claude (WRITE_SCRIPT)."""

    requires_key = True

    def _api_key(self, ctx: CallContext) -> str:
        key_ref = self.config.auth.key_ref or "anthropic"
        key = ctx.keys.get(ctx.user_id, key_ref)
        if not key:
            raise InvalidKeyError(f"No Anthropic key for user {ctx.user_id}")
        return key

    def _client(self, ctx: CallContext) -> Any:
        from anthropic import AsyncAnthropic

        return AsyncAnthropic(api_key=self._api_key(ctx))

    def _default_model(self) -> str:
        block = self.config.tasks.get(Task.WRITE_SCRIPT.value, {}) or {}
        return block.get("default_model") or "claude-3-7-sonnet-latest"

    def _max_tokens(self) -> int:
        block = self.config.tasks.get(Task.WRITE_SCRIPT.value, {}) or {}
        return int(block.get("max_tokens", 8192))

    # ---- validate ----------------------------------------------------------
    async def validate_key(self, ctx: CallContext) -> bool:
        """Cheap test call: a 1-token completion."""
        from anthropic import APIStatusError, AuthenticationError

        client = self._client(ctx)
        try:
            await client.messages.create(
                model=self._default_model(),
                max_tokens=1,
                messages=[{"role": "user", "content": "ping"}],
            )
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
        from anthropic import APIStatusError, AuthenticationError

        client = self._client(ctx)
        model = req.model or self._default_model()

        messages: list[dict[str, Any]] = []
        for m in req.messages:
            role = m.get("role", "user")
            role = "assistant" if role in ("assistant", "ai", "model") else "user"
            messages.append({"role": role, "content": str(m.get("content", ""))})

        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": self._max_tokens(),
            "messages": messages,
        }
        if req.system:
            kwargs["system"] = req.system
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.json_schema:
            kwargs["tools"] = [
                {
                    "name": _TOOL_NAME,
                    "description": "Emit the requested structured script as JSON.",
                    "input_schema": req.json_schema,
                }
            ]
            kwargs["tool_choice"] = {"type": "tool", "name": _TOOL_NAME}

        try:
            resp = await client.messages.create(**kwargs)
        except AuthenticationError as exc:
            raise InvalidKeyError(str(exc)) from exc
        except APIStatusError as exc:
            if exc.status_code in (401, 403):
                raise InvalidKeyError(str(exc)) from exc
            raise ProviderUnavailableError(str(exc)) from exc
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailableError(str(exc)) from exc

        text = _extract_text(resp, want_json=bool(req.json_schema))
        usage = _map_anthropic_usage(resp)
        cost = compute_cost(
            Task.WRITE_SCRIPT.value, float(usage.get("total_tokens", 0)), self.config.pricing
        )
        ctx.usage.record(
            ctx.user_id, self.provider_id, Task.WRITE_SCRIPT.value,
            float(usage.get("total_tokens", 0)), cost,
        )
        return ScriptResult(text=text, model=model, usage=usage, raw=None)


def _extract_text(resp: Any, *, want_json: bool) -> str:
    """Pull text out of an Anthropic message; serialize tool input when JSON."""
    for block in getattr(resp, "content", []) or []:
        btype = getattr(block, "type", None)
        if want_json and btype == "tool_use":
            return json.dumps(getattr(block, "input", {}), ensure_ascii=False)
    # fall back to first text block
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    return ""


def _map_anthropic_usage(resp: Any) -> dict[str, Any]:
    u = getattr(resp, "usage", None)
    if u is None:
        return {}
    prompt = getattr(u, "input_tokens", 0) or 0
    completion = getattr(u, "output_tokens", 0) or 0
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


__all__ = ["ClaudeClient"]
