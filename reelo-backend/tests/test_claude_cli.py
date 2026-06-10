"""claude-cli (BYO subscription) script provider — fully mocked, no real CLI.

Covers: argv + env assembly (token via CLAUDE_CODE_OAUTH_TOKEN, isolated
CLAUDE_CONFIG_DIR, model, output-format, single-turn/no-tools, system prompt),
result-text parsing + usage mapping, missing-token -> InvalidKeyError, auth
failure (exit!=0 + 401) -> InvalidKeyError, other non-zero exit ->
ProviderUnavailableError, and that the registry resolves the provider + maps its
key_ref. NEVER invokes the real `claude` binary.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

import clients.claude_cli as claude_cli
from clients.base import (
    CallContext,
    InvalidKeyError,
    ProviderUnavailableError,
    ScriptRequest,
    ServiceConfig,
    Task,
)
from clients.claude_cli import ClaudeCliClient, _build_prompt, _map_cli_usage, _parse_output
from clients.registry import ServiceRegistry
from keystore import Cipher, KeyStore
from usage import UsageLogger

_CFG = ServiceConfig(
    provider_id="claude-cli",
    raw={
        "auth": {"type": "key", "key_ref": "claude_oauth"},
        "tasks": {
            "write-script": {
                "default_model": "claude-sonnet-4-6",
                "supports_json_mode": False,
                "supports_system_prompt": True,
                "timeout": 300,
            }
        },
        "pricing": {"write-script": {"per_1k_input": 0.0, "per_1k_output": 0.0}},
    },
)


def _ctx(*, with_token: bool = True) -> CallContext:
    store = KeyStore(Cipher(b"k" * 32))
    if with_token:
        store.save("u1", "claude_oauth", "sk-ant-oat01-SECRET")
    return CallContext(user_id="u1", keys=store, usage=UsageLogger())


# --------------------------------------------------------------------------- #
# A fake asyncio subprocess that records argv + env and returns canned output  #
# --------------------------------------------------------------------------- #
class _FakeProc:
    def __init__(self, stdout: bytes, stderr: bytes, returncode: int) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self):
        return self._stdout, self._stderr

    def kill(self):  # pragma: no cover - only hit on timeout path
        pass

    async def wait(self):  # pragma: no cover
        return self.returncode


def _patch_exec(monkeypatch, *, stdout: dict | str, stderr: str = "", returncode: int = 0):
    """Patch asyncio.create_subprocess_exec; capture the argv + env it was given."""
    captured: dict = {}
    out_body = (stdout if isinstance(stdout, str) else json.dumps(stdout)).encode()
    err_body = stderr.encode()

    # NB: the real callee passes stdout=/stderr=PIPE kwargs; swallow them under
    # different names so they don't shadow the canned bytes captured above.
    async def fake_exec(*argv, env=None, **kw):
        captured["argv"] = list(argv)
        captured["env"] = dict(env or {})
        captured["kwargs"] = dict(kw)
        return _FakeProc(out_body, err_body, returncode)

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)
    # Pretend the binary exists so the PATH guard passes.
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _b: "/usr/bin/claude")
    return captured


_OK_RESULT = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "api_error_status": None,
    "result": '{"ok": true}',
    "usage": {"input_tokens": 12, "output_tokens": 8},
    "modelUsage": {"claude-sonnet-4-6": {"inputTokens": 12, "outputTokens": 8}},
}


# ---- pure helpers ---------------------------------------------------------- #
def test_build_prompt_flattens_messages_and_adds_json_nudge():
    req = ScriptRequest(
        messages=[
            {"role": "user", "content": "Write a script"},
            {"role": "assistant", "content": "Sure"},
            {"role": "user", "content": "Make it shorter"},
        ],
        system="be terse",
        json_schema={"type": "object"},
    )
    prompt = _build_prompt(req)
    assert "User: Write a script" in prompt
    assert "Assistant: Sure" in prompt
    assert "User: Make it shorter" in prompt
    # system goes through --system-prompt, NOT folded into the prompt body
    assert "be terse" not in prompt
    # json schema set -> a JSON nudge is appended
    assert "JSON" in prompt


def test_map_cli_usage():
    u = _map_cli_usage(_OK_RESULT)
    assert u == {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20}


def test_parse_output_handles_trailing_log_line():
    raw = "some warning line\n" + json.dumps(_OK_RESULT)
    parsed = _parse_output(raw)
    assert parsed is not None and parsed["result"] == '{"ok": true}'


# ---- write_script happy path: argv + env + parse --------------------------- #
async def test_write_script_builds_argv_env_and_parses(monkeypatch):
    captured = _patch_exec(monkeypatch, stdout=_OK_RESULT)
    ctx = _ctx()
    req = ScriptRequest(
        messages=[{"role": "user", "content": "write"}],
        system="you are a scriptwriter",
        model="claude-opus-4-8",
        json_schema={"type": "object"},
    )
    res = await ClaudeCliClient(_CFG).write_script(req, ctx)

    # parsed result text comes straight from the CLI `result` field
    assert json.loads(res.text) == {"ok": True}
    assert res.model == "claude-opus-4-8"
    assert res.usage["total_tokens"] == 20

    argv = captured["argv"]
    # headless print mode + json output + the requested model
    assert "-p" in argv
    assert argv[argv.index("--output-format") + 1] == "json"
    assert argv[argv.index("--model") + 1] == "claude-opus-4-8"
    # single-turn guard: tools disabled so the model can't go agentic/multi-turn
    assert "--tools" in argv and argv[argv.index("--tools") + 1] == ""
    # real system prompt forwarded via --system-prompt
    assert argv[argv.index("--system-prompt") + 1] == "you are a scriptwriter"
    # BƯỚC 0 bisect: these flags add nothing (and --max-turns 1 is harmful) so
    # the minimal invocation must NOT include them.
    assert "--max-turns" not in argv
    assert "--permission-mode" not in argv
    assert "--no-session-persistence" not in argv

    env = captured["env"]
    # the user's subscription token is injected (and isolated config dir set)
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "sk-ant-oat01-SECRET"
    assert env["CLAUDE_CONFIG_DIR"].startswith(__import__("tempfile").gettempdir())
    # a metered API key in the host env must not shadow the OAuth token
    assert env["ANTHROPIC_API_KEY"] == ""

    # usage recorded (zero cost — billed to the user's subscription)
    ev = ctx.usage._sink.events[0]  # type: ignore[attr-defined]
    assert ev.units == 20 and ev.cost == 0.0


async def test_write_script_default_model_when_unset(monkeypatch):
    captured = _patch_exec(monkeypatch, stdout=_OK_RESULT)
    req = ScriptRequest(messages=[{"role": "user", "content": "hi"}])
    await ClaudeCliClient(_CFG).write_script(req, _ctx())
    assert captured["argv"][captured["argv"].index("--model") + 1] == "claude-sonnet-4-6"


# ---- error paths ----------------------------------------------------------- #
async def test_missing_token_raises_invalid_key(monkeypatch):
    # No subprocess should ever be spawned without a token.
    _patch_exec(monkeypatch, stdout=_OK_RESULT)
    with pytest.raises(InvalidKeyError):
        await ClaudeCliClient(_CFG).write_script(
            ScriptRequest(messages=[{"role": "user", "content": "x"}]), _ctx(with_token=False)
        )


async def test_auth_failure_exit_nonzero_raises_invalid_key(monkeypatch):
    bad = {
        "type": "result",
        "is_error": True,
        "api_error_status": 401,
        "result": "Failed to authenticate. API Error: 401 Invalid bearer token",
    }
    _patch_exec(monkeypatch, stdout=bad, returncode=1)
    with pytest.raises(InvalidKeyError):
        await ClaudeCliClient(_CFG).write_script(
            ScriptRequest(messages=[{"role": "user", "content": "x"}]), _ctx()
        )


async def test_not_logged_in_message_raises_invalid_key(monkeypatch):
    bad = {"type": "result", "is_error": True, "result": "Not logged in · Please run /login"}
    _patch_exec(monkeypatch, stdout=bad, returncode=1)
    with pytest.raises(InvalidKeyError):
        await ClaudeCliClient(_CFG).write_script(
            ScriptRequest(messages=[{"role": "user", "content": "x"}]), _ctx()
        )


async def test_other_nonzero_exit_raises_unavailable(monkeypatch):
    _patch_exec(monkeypatch, stdout="", stderr="boom: overloaded", returncode=1)
    with pytest.raises(ProviderUnavailableError) as exc:
        await ClaudeCliClient(_CFG).write_script(
            ScriptRequest(messages=[{"role": "user", "content": "x"}]), _ctx()
        )
    assert not isinstance(exc.value, InvalidKeyError)
    assert "boom" in str(exc.value)


async def test_missing_binary_raises_unavailable(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _b: None)
    with pytest.raises(ProviderUnavailableError):
        await ClaudeCliClient(_CFG).write_script(
            ScriptRequest(messages=[{"role": "user", "content": "x"}]), _ctx()
        )


async def test_validate_key_ok(monkeypatch):
    _patch_exec(monkeypatch, stdout=_OK_RESULT)
    assert await ClaudeCliClient(_CFG).validate_key(_ctx()) is True


async def test_validate_key_bad_token(monkeypatch):
    bad = {"type": "result", "is_error": True, "api_error_status": 401, "result": "bad bearer token"}
    _patch_exec(monkeypatch, stdout=bad, returncode=1)
    with pytest.raises(InvalidKeyError):
        await ClaudeCliClient(_CFG).validate_key(_ctx())


# ---- availability ---------------------------------------------------------- #
async def test_is_available_requires_token():
    client = ClaudeCliClient(_CFG)
    assert await client.is_available(_ctx()) is True
    assert await client.is_available(_ctx(with_token=False)) is False


# ---- registry wiring ------------------------------------------------------- #
def _registry(tmp_path: Path) -> ServiceRegistry:
    yaml_text = textwrap.dedent(
        """
        services:
          claude-cli:
            display_name: "Claude (subscription)"
            client: "clients.claude_cli.ClaudeCliClient"
            cost_tier: free
            auth: { type: key, key_ref: "claude_oauth" }
            tasks: { write-script: { default_model: "claude-sonnet-4-6" } }
            pricing: { write-script: { per_1k_input: 0.0, per_1k_output: 0.0 } }
        routing:
          fallback: { write-script: [] }
        """
    )
    path = tmp_path / "services.yaml"
    path.write_text(yaml_text, encoding="utf-8")
    return ServiceRegistry(path)


# ---- headless robustness: stdin / config seed / fail-fast (BƯỚC 0) --------- #
async def test_stdin_is_closed_devnull(monkeypatch):
    """Regression for the production hang: stdin MUST be DEVNULL so the CLI never
    blocks waiting for stdin data in ``-p`` mode."""
    import asyncio as _asyncio

    captured = _patch_exec(monkeypatch, stdout=_OK_RESULT)
    await ClaudeCliClient(_CFG).write_script(
        ScriptRequest(messages=[{"role": "user", "content": "x"}]), _ctx()
    )
    assert captured["kwargs"].get("stdin") == _asyncio.subprocess.DEVNULL


async def test_config_dir_is_isolated_and_not_seeded(monkeypatch):
    """The CLI runs against an isolated per-call CLAUDE_CONFIG_DIR. BƯỚC 0 proved an
    empty/non-seeded dir runs fine headless, so we no longer write a `.claude.json`
    seed (it reached the same auth check in the same time — pure superstition)."""
    import tempfile as _tempfile

    captured = _patch_exec(monkeypatch, stdout=_OK_RESULT)

    seen_dirs: list[str] = []
    real_mkdtemp = claude_cli.tempfile.mkdtemp

    def _spy_mkdtemp(*a, **k):
        d = real_mkdtemp(*a, **k)
        seen_dirs.append(d)
        return d

    monkeypatch.setattr(claude_cli.tempfile, "mkdtemp", _spy_mkdtemp)
    await ClaudeCliClient(_CFG).write_script(
        ScriptRequest(messages=[{"role": "user", "content": "x"}]), _ctx()
    )
    # the env points the CLI at an isolated temp config dir
    assert captured["env"]["CLAUDE_CONFIG_DIR"].startswith(_tempfile.gettempdir())
    # no seed function exists anymore, and nothing was written into the temp dir
    assert not hasattr(claude_cli, "_seed_config_dir")
    # the dir is removed after the call, so we can only assert it was the one used
    assert seen_dirs and captured["env"]["CLAUDE_CONFIG_DIR"] == seen_dirs[0]


async def test_timeout_kills_process_and_raises_unavailable(monkeypatch):
    """A wedged CLI (communicate never returns) must be killed and surfaced as
    ProviderUnavailableError fast — NOT hang to the arq job timeout."""
    import asyncio as _asyncio

    killed = {"kill": False, "waited": False}

    class _HangingProc:
        returncode = None

        async def communicate(self):
            await _asyncio.sleep(3600)  # never returns within the call timeout

        def kill(self):
            killed["kill"] = True

        async def wait(self):
            killed["waited"] = True
            return -9

    async def fake_exec(*argv, env=None, **kw):
        return _HangingProc()

    monkeypatch.setattr(claude_cli.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(claude_cli.shutil, "which", lambda _b: "/usr/bin/claude")

    with pytest.raises(ProviderUnavailableError) as exc:
        await claude_cli._run_claude(
            prompt="x",
            token="sk-ant-oat01-x",
            model="claude-sonnet-4-6",
            binary="claude",
            timeout=0.05,
        )
    assert not isinstance(exc.value, InvalidKeyError)  # not an auth error
    assert killed["kill"] and killed["waited"]  # process reaped, not orphaned
    assert "did not respond" in str(exc.value)


def test_timeout_is_clamped_to_max_call_timeout():
    """Even if services.yaml configures a large timeout, the per-call cap is
    clamped so a wedged call can't eat the whole job_timeout."""
    cfg = ServiceConfig(
        provider_id="claude-cli",
        raw={"tasks": {"write-script": {"timeout": 9999}}},
    )
    assert ClaudeCliClient(cfg)._timeout() == claude_cli._MAX_CALL_TIMEOUT


async def test_registry_resolves_claude_cli(tmp_path):
    reg = _registry(tmp_path)
    store = KeyStore(Cipher(b"k" * 32))
    store.save("u1", "claude_oauth", "sk-ant-oat01-x")
    ctx = CallContext(user_id="u1", keys=store, usage=UsageLogger())
    client = await reg.resolve(Task.WRITE_SCRIPT, "claude-cli", ctx)
    assert client.provider_id == "claude-cli"
    # provider id <-> key_ref mapping (used by /keys + setup.tsx saveApiKey)
    assert reg.key_ref_for_provider("claude-cli") == "claude_oauth"
    assert reg.provider_for_key_ref("claude_oauth") == "claude-cli"


def test_real_catalog_registers_claude_cli():
    """The bundled services.yaml imports ClaudeCliClient cleanly + maps key_ref."""
    from clients.registry import get_registry

    reg = get_registry()
    assert reg.try_get("claude-cli") is not None
    assert reg.key_ref_for_provider("claude-cli") == "claude_oauth"
    # explicit choice only: NOT in the write-script fallback chain
    assert "claude-cli" not in reg._fallback.get("write-script", [])  # type: ignore[attr-defined]
