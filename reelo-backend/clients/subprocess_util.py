"""Shared helper to run skill ``scripts/*.py`` as subprocesses (Module 3).

The skill-wrapper clients (:mod:`clients.skill_voice`, :mod:`clients.skill_image`)
shell out to the standalone scripts that ship with the
``skill-tao-video-Youtube-ton-giao`` skill (``generate_voice.py`` /
``generate_image.py``). Those scripts print a JSON metadata blob on stdout and
exit non-zero on failure. Module 2 also uses this helper for ``render.py``, so it
lives in a shared location.

BYOK keys are injected just-in-time through ``env`` (built by
``KeyStore.as_env``); they are never written to disk or logged here.

The skill root is resolved from ``REELO_SKILL_ROOT`` if set, otherwise from the
repository layout (``<repo>/skill-tao-video-Youtube-ton-giao``). It can also be
overridden per call (used by tests).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from clients.base import ProviderUnavailableError


class SubprocessError(ProviderUnavailableError):
    """A skill script exited non-zero. Carries the captured stderr for triage.

    Subclasses :class:`ProviderUnavailableError` so a failing skill script is
    eligible for BYOK-aware fallback like any other provider outage.
    """

    def __init__(self, script: str, returncode: int, stderr: str) -> None:
        self.script = script
        self.returncode = returncode
        self.stderr = stderr
        super().__init__(f"{script} exited {returncode}: {stderr.strip()[:500]}")


def default_skill_root() -> Path:
    """Resolve the skill root directory.

    Order: ``REELO_SKILL_ROOT`` env var → repo-relative default
    (``<repo>/skill-tao-video-Youtube-ton-giao``).
    """
    env_root = os.environ.get("REELO_SKILL_ROOT")
    if env_root:
        return Path(env_root)
    # clients/ -> reelo-backend/ -> <repo>/
    repo_root = Path(__file__).resolve().parent.parent.parent
    return repo_root / "skill-tao-video-Youtube-ton-giao"


async def run_skill_script(
    script: str,
    args: list[str],
    env: dict[str, str] | None = None,
    *,
    skill_root: Path | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run ``scripts/<script>`` under the skill root and parse its stdout JSON.

    Args:
        script: Script filename, e.g. ``"generate_voice.py"``.
        args: CLI args passed after the script path.
        env: Extra environment variables (merged over ``os.environ``); used to
            inject the user's BYOK key for the current run.
        skill_root: Override the skill root (tests).
        timeout: Optional wall-clock timeout in seconds.

    Returns:
        The parsed JSON object the script printed on stdout.

    Raises:
        SubprocessError: if the script exits non-zero (eligible for fallback).
        ProviderUnavailableError: on timeout or unparseable output.
    """
    root = skill_root or default_skill_root()
    script_path = root / "scripts" / script
    full_env = {**os.environ, **(env or {})}

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        str(script_path),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(root),
        env=full_env,
    )
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        proc.kill()
        await proc.wait()
        raise ProviderUnavailableError(f"{script} timed out after {timeout}s") from exc

    out = out_b.decode("utf-8", errors="replace")
    err = err_b.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        raise SubprocessError(script, proc.returncode or -1, err)

    try:
        return json.loads(out)
    except json.JSONDecodeError as exc:
        raise ProviderUnavailableError(
            f"{script} produced non-JSON stdout: {out.strip()[:300]}"
        ) from exc


__all__ = ["SubprocessError", "run_skill_script", "default_skill_root"]
