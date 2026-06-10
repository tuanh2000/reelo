"""Claude via the ``claude`` CLI on a user's BYO subscription token (Module 3).

Capability: WRITE_SCRIPT. **NOT** the metered Anthropic API key — this provider
shells out to the ``claude`` Code CLI in headless/print mode (``claude -p``)
authenticated by the user's OWN Claude subscription OAuth token (created with
``claude setup-token``). Reelo does NOT provide Claude: each user brings their own
account; the token only serves its own owner (BYO subscription). See the ToS
caveat in ``docs/module-3-ai-service-manager.md``.

Family: subscription-CLI. We do NOT reuse :func:`run_skill_script` (that parses a
skill-specific JSON shape and runs ``python``); instead we ``asyncio``-exec the
``claude`` binary directly and parse its native ``--output-format json`` result.

Verified against ``claude`` CLI 2.1.x (BƯỚC 0):
- Auth headless: env ``CLAUDE_CODE_OAUTH_TOKEN=<token>`` used as a bearer token.
  We also pin ``CLAUDE_CONFIG_DIR`` to a per-user temp dir so one user's token
  never reads another user's keychain/session (and an invalid token fails clean
  with ``api_error_status: 401`` instead of silently falling back to a logged-in
  machine account).
- Single-turn, no tools/agentic: ``--tools "" --max-turns 1 --permission-mode
  default`` (``--system-prompt`` carries ``req.system``; no CLAUDE.md/agent).
- ``--output-format json`` prints ONE object on stdout with:
  ``{type:"result", is_error:bool, result:"<assistant text>", modelUsage:{...},
  usage:{input_tokens, output_tokens, ...}}``. Exit code 0 on success; non-zero
  + ``is_error:true`` on auth/other failure.

json_schema (structured output): the CLI has ``--json-schema`` but to stay
identical to the other providers' RULE/sentinel fallback (Module 1 already
parses JSON out of plain text), we only ask for JSON in the prompt and return the
``result`` text. Module 1's parser does the rest.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
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

# The user's subscription OAuth token (from ``claude setup-token``) is injected
# under this env var; the CLI uses it as a bearer token in headless mode.
_TOKEN_ENV = "CLAUDE_CODE_OAUTH_TOKEN"
_KEY_REF_DEFAULT = "claude_oauth"
_DEFAULT_MODEL = "claude-sonnet-4-6"
_DEFAULT_TIMEOUT = 300.0  # seconds — scriptwriting can be a long single turn


class ClaudeCliClient(AIClient):
    """Claude over the ``claude`` CLI on a user's BYO subscription (WRITE_SCRIPT)."""

    capabilities = {Task.WRITE_SCRIPT}
    cost_tier = "free"  # billed to the user's subscription, not metered by Reelo
    requires_key = True
    provider_id = "claude-cli"

    # ---- config helpers ----------------------------------------------------
    def _key_ref(self) -> str:
        return self.config.auth.key_ref or _KEY_REF_DEFAULT

    def _token(self, ctx: CallContext) -> str:
        token = ctx.keys.get(ctx.user_id, self._key_ref())
        if not token:
            raise InvalidKeyError(f"No Claude subscription token for user {ctx.user_id}")
        return token

    def _default_model(self) -> str:
        block = self.config.tasks.get(Task.WRITE_SCRIPT.value, {}) or {}
        return block.get("default_model") or _DEFAULT_MODEL

    def _timeout(self) -> float:
        block = self.config.tasks.get(Task.WRITE_SCRIPT.value, {}) or {}
        return float(block.get("timeout", self.config.raw.get("timeout", _DEFAULT_TIMEOUT)))

    @staticmethod
    def _binary() -> str:
        return os.environ.get("REELO_CLAUDE_BIN") or "claude"

    # ---- availability / validate ------------------------------------------
    async def is_available(self, ctx: CallContext) -> bool:
        """Available iff the user has stored a subscription token (key present)."""
        return ctx.keys.has(ctx.user_id, self._key_ref())

    async def validate_key(self, ctx: CallContext) -> bool:
        """Cheap test: a 1-turn ``ping`` with the token; exit 0 + not is_error -> valid."""
        token = self._token(ctx)
        try:
            await _run_claude(
                prompt="Reply with the single word: ok",
                token=token,
                model=self._default_model(),
                binary=self._binary(),
                timeout=min(self._timeout(), 120.0),
            )
        except InvalidKeyError:
            raise
        except ProviderUnavailableError:
            # Service hiccup / overload at validate time -> let the caller store
            # the key unverified (the /keys router maps this to valid=None).
            raise
        return True

    # ---- write-script ------------------------------------------------------
    async def write_script(self, req: ScriptRequest, ctx: CallContext) -> ScriptResult:
        token = self._token(ctx)
        model = req.model or self._default_model()
        prompt = _build_prompt(req)

        result = await _run_claude(
            prompt=prompt,
            token=token,
            model=model,
            binary=self._binary(),
            system=req.system,
            json_mode=bool(req.json_schema),
            timeout=self._timeout(),
        )

        text = str(result.get("result", "") or "")
        usage = _map_cli_usage(result)
        cost = compute_cost(
            Task.WRITE_SCRIPT.value, float(usage.get("total_tokens", 0)), self.config.pricing
        )
        ctx.usage.record(
            ctx.user_id,
            self.provider_id,
            Task.WRITE_SCRIPT.value,
            float(usage.get("total_tokens", 0)),
            cost,
        )
        return ScriptResult(text=text, model=model, usage=usage, raw=None)


# --------------------------------------------------------------------------- #
# Prompt assembly + subprocess runner (no run_skill_script — native CLI parse) #
# --------------------------------------------------------------------------- #
def _build_prompt(req: ScriptRequest) -> str:
    """Flatten ``req.messages`` into one prompt for ``claude -p``.

    ``req.system`` goes through ``--system-prompt`` (a real system prompt), so it
    is NOT folded in here. A trailing JSON nudge is added when a schema is set
    (the CLI has no ``response_format``; Module 1 parses JSON from the text).
    """
    parts: list[str] = []
    for m in req.messages:
        role = str(m.get("role", "user")).lower()
        speaker = "Assistant" if role in ("assistant", "ai", "model") else "User"
        content = str(m.get("content", "")).strip()
        if content:
            parts.append(f"{speaker}: {content}")
    prompt = "\n\n".join(parts) if parts else ""
    if req.json_schema:
        prompt += (
            "\n\nRespond with ONLY a single valid JSON object that conforms to the "
            "requested structure. Do not wrap it in markdown fences or add prose."
        )
    return prompt


def _build_argv(
    *,
    prompt: str,
    model: str,
    binary: str,
    system: str | None,
    json_mode: bool,
) -> list[str]:
    """The ``claude`` argv for a single-turn, no-tool headless run."""
    argv = [
        binary,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--model",
        model,
        # No agentic loop / tools: one assistant turn, no Bash/Edit/etc.
        "--tools",
        "",
        "--max-turns",
        "1",
        "--permission-mode",
        "default",
        # Stable prompt cache + no session files written to disk.
        "--no-session-persistence",
    ]
    if system:
        argv += ["--system-prompt", system]
    return argv


async def _run_claude(
    *,
    prompt: str,
    token: str,
    model: str,
    binary: str,
    system: str | None = None,
    json_mode: bool = False,
    timeout: float = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Exec ``claude`` headless with the user's token and parse the JSON result.

    The token is injected via ``CLAUDE_CODE_OAUTH_TOKEN`` and an isolated
    ``CLAUDE_CONFIG_DIR`` (per-call temp dir) so users never share auth state and
    the token is the sole credential. Never logs the token or prompt.

    Raises:
        InvalidKeyError: the token was rejected (auth 401/403, "Not logged in").
        ProviderUnavailableError: CLI missing, timeout, non-zero exit, bad output,
            or an ``is_error`` result that isn't an auth failure.
    """
    if shutil.which(binary) is None and not os.path.isabs(binary):
        raise ProviderUnavailableError(
            f"claude CLI ('{binary}') not found on PATH — install @anthropic-ai/claude-code"
        )

    argv = _build_argv(
        prompt=prompt, model=model, binary=binary, system=system, json_mode=json_mode
    )

    config_dir = tempfile.mkdtemp(prefix="reelo-claude-")
    env = {
        **os.environ,
        _TOKEN_ENV: token,
        "CLAUDE_CONFIG_DIR": config_dir,
        # Defensive: make sure a metered API key in the host env can't shadow the
        # subscription OAuth token we want the CLI to use.
        "ANTHROPIC_API_KEY": "",
    }

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError as exc:
            proc.kill()
            await proc.wait()
            raise ProviderUnavailableError(
                f"claude CLI timed out after {timeout}s"
            ) from exc
    finally:
        shutil.rmtree(config_dir, ignore_errors=True)

    out = out_b.decode("utf-8", errors="replace")
    err = err_b.decode("utf-8", errors="replace")
    returncode = proc.returncode or 0

    data = _parse_output(out)

    # The CLI reports auth/other problems both via exit code and is_error in the
    # JSON envelope. Classify auth failures as InvalidKeyError (no fallback).
    if returncode != 0 or (isinstance(data, dict) and data.get("is_error")):
        message = ""
        if isinstance(data, dict):
            message = str(data.get("result") or "")
            status = data.get("api_error_status")
        else:
            status = None
        detail = message or err.strip() or out.strip() or "unknown error"
        if status in (401, 403) or _looks_like_auth_failure(detail):
            raise InvalidKeyError(f"claude CLI auth failed: {detail[:300]}")
        raise ProviderUnavailableError(f"claude CLI exited {returncode}: {detail[:500]}")

    if not isinstance(data, dict):
        raise ProviderUnavailableError(
            f"claude CLI produced unexpected output: {out.strip()[:300]}"
        )
    return data


def _parse_output(out: str) -> dict[str, Any] | None:
    """Parse the single JSON result object the CLI prints on stdout."""
    out = out.strip()
    if not out:
        return None
    try:
        parsed = json.loads(out)
    except json.JSONDecodeError:
        # Be lenient: some environments may emit a trailing log line.
        for line in reversed(out.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    parsed = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue
        else:
            return None
    return parsed if isinstance(parsed, dict) else None


def _looks_like_auth_failure(text: str) -> bool:
    low = text.lower()
    return (
        "not logged in" in low
        or "invalid bearer token" in low
        or "failed to authenticate" in low
        or "please run /login" in low
        or "unauthorized" in low
    )


def _map_cli_usage(result: dict[str, Any]) -> dict[str, Any]:
    """Map the CLI's ``usage`` block to the cross-provider token shape."""
    usage = result.get("usage") or {}
    prompt = int(usage.get("input_tokens", 0) or 0)
    completion = int(usage.get("output_tokens", 0) or 0)
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": prompt + completion,
    }


__all__ = ["ClaudeCliClient"]
