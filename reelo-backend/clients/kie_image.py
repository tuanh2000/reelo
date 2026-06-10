"""kie.ai native image client (Module 3) — async submit/poll/download via httpx.

kie.ai's ``gpt-image-2`` generates **asynchronously**:

1. ``POST /jobs/createTask`` → returns a ``taskId`` (credit is spent here; the job
   is then queued + run on kie's servers, INDEPENDENT of this process).
2. ``GET /jobs/recordInfo?taskId=…`` → polls the task until the image URL is ready.
3. download the URL.

Because the task survives a worker restart, persisting the ``taskId`` lets a
resumed produce run **fetch an in-flight image instead of re-submitting** — i.e.
not re-spend credit on an image kie is already (or already finished) generating.
This native client therefore exposes the three steps separately so the produce
runner can persist the taskId the moment it is created and reuse it on resume:

- :meth:`submit_image_task` → ``taskId`` (one createTask call; spends credit).
- :meth:`poll_image_task`   → :class:`ImageResult` once ready (raises
  :class:`KieTaskGone` if the task failed / is unknown-or-expired so the caller
  can re-submit; :class:`KieTaskPending` if still generating after ``max_wait``).
- :meth:`generate_image`    → submit + poll + download in one shot (the standard
  path for callers without resume state, e.g. thumbnails / un-tracked runs).

Ported faithfully from the skill ``generate_image.py`` (same endpoints, model,
aspect-ratio strings, success/failure states, and result-URL shape walking) but
**async + key-from-ctx** (no subprocess / env var).
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from clients.base import (
    AIClient,
    CallContext,
    ImageRequest,
    ImageResult,
    InvalidKeyError,
    ProviderUnavailableError,
    Task,
)
from usage import compute_cost

log = logging.getLogger("reelo.clients.kie")

KIE_BASE = "https://api.kie.ai/api/v1"
MODEL = "gpt-image-2-text-to-image"
# kie.ai's gpt-image-2 supports aspect-ratio strings (not pixel dimensions).
VALID_SIZES = {"1:1", "3:2", "2:3", "4:3", "3:4", "16:9", "9:16"}
SUCCESS_STATES = {"success", "succeed", "succeeded", "completed", "complete", "done"}
FAILURE_STATES = {"failed", "fail", "error", "generate_failed"}

DEFAULT_MAX_WAIT = 300
DEFAULT_POLL_INTERVAL = 5
_SUBMIT_TIMEOUT = 60
_POLL_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 120


class KieTaskGone(ProviderUnavailableError):
    """The persisted task no longer yields an image (failed / unknown / expired).

    Signals the runner to **re-submit** a fresh task rather than error the segment
    (the credit for the gone task is already lost; resubmitting is the only path).
    """


class KieTaskPending(ProviderUnavailableError):
    """The task is still generating after ``max_wait`` — try again (do NOT resubmit).

    The taskId stays valid + persisted, so a later resume can fetch it without
    re-spending credit. The runner surfaces this as a normal segment error.
    """


def extract_image_url(record_data: dict) -> str | None:
    """Pull an image URL out of a ``recordInfo`` response (state == success).

    kie returns the result inside ``resultJson`` which may be a JSON string or an
    object, holding a bare URL, a list, or a dict under one of several keys — walk
    the common shapes (faithful port of the skill script).
    """
    result_json = record_data.get("resultJson") or ""
    if not result_json:
        return None
    if isinstance(result_json, str):
        try:
            parsed: Any = json.loads(result_json)
        except json.JSONDecodeError:
            return result_json if result_json.startswith("http") else None
    else:
        parsed = result_json

    if isinstance(parsed, str) and parsed.startswith("http"):
        return parsed
    if isinstance(parsed, list) and parsed:
        first = parsed[0]
        if isinstance(first, str):
            return first
        if isinstance(first, dict):
            return first.get("url")
        return None
    if isinstance(parsed, dict):
        for key in ("resultUrls", "urls", "images", "imageUrls", "output", "image_url", "url"):
            val = parsed.get(key)
            if isinstance(val, list) and val:
                first = val[0]
                if isinstance(first, str):
                    return first
                if isinstance(first, dict):
                    return first.get("url") or first.get("imageUrl") or first.get("imageURL")
            if isinstance(val, str) and val.startswith("http"):
                return val
    return None


class KieImageClient(AIClient):
    """kie.ai text-to-image via an async submit→poll→download task lifecycle."""

    requires_key = True
    capabilities = {Task.GENERATE_IMAGE}
    # Marks this client as supporting the persist-taskId / fetch-on-resume flow so
    # the produce runner reuses an in-flight task instead of re-spending credit.
    supports_async_image_tasks = True

    # ---- key / size --------------------------------------------------------
    def _api_key(self, ctx: CallContext) -> str:
        key_ref = self.config.auth.key_ref or "kie"
        key = ctx.keys.get(ctx.user_id, key_ref)
        if not key:
            raise InvalidKeyError(f"No kie.ai key for user {ctx.user_id}")
        return key

    def _resolve_size(self, size: str) -> str:
        if size in VALID_SIZES:
            return size
        block = self.config.tasks.get(Task.GENERATE_IMAGE.value, {}) or {}
        return block.get("default_size", "16:9")

    def _prompt(self, req: ImageRequest) -> str:
        if req.prompt:
            text = req.prompt
        elif req.prompt_file:
            text = Path(req.prompt_file).read_text(encoding="utf-8").strip()
        else:
            raise ProviderUnavailableError("ImageRequest needs prompt or prompt_file")
        if not text.strip():
            raise ProviderUnavailableError("kie.ai image prompt is empty")
        return text

    # ---- validate ----------------------------------------------------------
    async def validate_key(self, ctx: CallContext) -> bool:
        """Presence check — kie has no cheap auth-only endpoint (real gen spends
        credit). A wrong key surfaces as :class:`InvalidKeyError` on first submit."""
        if not ctx.keys.get(ctx.user_id, self.config.auth.key_ref or "kie"):
            raise InvalidKeyError(f"No kie.ai key for user {ctx.user_id}")
        return True

    # ---- step 1: create task (spends credit) -------------------------------
    async def submit_image_task(self, req: ImageRequest, ctx: CallContext) -> str:
        """Create a kie generation task and return its ``taskId``.

        Records the per-image cost here (createTask is the billable step). The
        caller should persist the returned taskId BEFORE polling so a crash leaves
        it recoverable.
        """
        import httpx

        api_key = self._api_key(ctx)
        body = {
            "model": MODEL,
            "input": {"prompt": self._prompt(req), "size": self._resolve_size(req.size)},
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        try:
            async with httpx.AsyncClient(timeout=_SUBMIT_TIMEOUT) as http:
                resp = await http.post(f"{KIE_BASE}/jobs/createTask", json=body, headers=headers)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"kie.ai create-task request failed: {exc}") from exc

        if resp.status_code in (401, 403):
            raise InvalidKeyError(f"kie.ai rejected the key (HTTP {resp.status_code})")
        if resp.status_code != 200:
            raise ProviderUnavailableError(
                f"kie.ai create-task HTTP {resp.status_code}: {resp.text[:300]}"
            )
        payload = resp.json()
        if payload.get("code") not in (200, 0):
            # 422 = permanent (bad size/prompt); anything else is provider-side.
            raise ProviderUnavailableError(f"kie.ai create-task error: {payload}")
        task_id = (payload.get("data") or {}).get("taskId")
        if not task_id:
            raise ProviderUnavailableError(f"kie.ai create-task returned no taskId: {payload}")

        cost = compute_cost(Task.GENERATE_IMAGE.value, 1.0, self.config.pricing)
        ctx.usage.record(ctx.user_id, self.provider_id, Task.GENERATE_IMAGE.value, 1.0, cost)
        return str(task_id)

    # ---- step 2: poll one record (no credit) -------------------------------
    async def fetch_image_task(self, task_id: str, ctx: CallContext) -> str | None:
        """One ``recordInfo`` poll. Returns the image URL when ready, else ``None``.

        Raises :class:`KieTaskGone` on a terminal failure state or an unreadable /
        unknown task (so the caller re-submits). A transient hiccup returns ``None``
        (treated as "still pending" by the polling loop).
        """
        import httpx

        api_key = self._api_key(ctx)
        headers = {"Authorization": f"Bearer {api_key}"}
        try:
            async with httpx.AsyncClient(timeout=_POLL_TIMEOUT) as http:
                resp = await http.get(
                    f"{KIE_BASE}/jobs/recordInfo",
                    params={"taskId": task_id},
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            log.warning("kie.ai recordInfo request failed (%s); will retry", exc)
            return None

        if resp.status_code in (401, 403):
            raise InvalidKeyError(f"kie.ai rejected the key (HTTP {resp.status_code})")
        if resp.status_code != 200:
            return None  # transient; keep polling
        payload = resp.json()
        if payload.get("code") not in (200, 0):
            # The task record can't be retrieved (unknown / expired) → gone.
            raise KieTaskGone(f"kie.ai recordInfo error for task {task_id}: {payload}")
        data = payload.get("data") or {}
        state = (data.get("state") or "").lower()
        if state in SUCCESS_STATES:
            url = extract_image_url(data)
            if url:
                return url
            raise KieTaskGone(f"kie.ai task {task_id} success but no image URL: {data}")
        if state in FAILURE_STATES:
            fail = data.get("failMsg") or data.get("failCode") or "unknown"
            raise KieTaskGone(f"kie.ai task {task_id} failed: {fail}")
        return None  # queued / generating

    # ---- step 2+3: poll until ready + download -----------------------------
    async def poll_image_task(
        self,
        task_id: str,
        out_path: Path,
        ctx: CallContext,
        *,
        max_wait: int = DEFAULT_MAX_WAIT,
        poll_interval: int = DEFAULT_POLL_INTERVAL,
    ) -> ImageResult:
        """Poll ``task_id`` until the image is ready, then download it.

        No credit is spent (recordInfo + download only) — this is the resume path's
        "is it already done on kie?" check. Raises :class:`KieTaskGone` (re-submit)
        or :class:`KieTaskPending` (still generating after ``max_wait``).
        """
        elapsed = 0
        while elapsed < max_wait:
            url = await self.fetch_image_task(task_id, ctx)
            if url is not None:
                await self._download(url, Path(out_path))
                return ImageResult(
                    out_path=Path(out_path), count=1,
                    raw={"task_id": task_id, "source_url": url, "model": MODEL},
                )
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
        raise KieTaskPending(f"kie.ai task {task_id} still generating after {max_wait}s")

    # ---- one-shot ----------------------------------------------------------
    async def generate_image(
        self, req: ImageRequest, out_path: Path, ctx: CallContext
    ) -> ImageResult:
        """Submit a fresh task, poll it to completion, and download (one shot)."""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        task_id = await self.submit_image_task(req, ctx)
        return await self.poll_image_task(task_id, out_path, ctx)

    async def _download(self, url: str, out_path: Path) -> None:
        import httpx

        out_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            async with httpx.AsyncClient(timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True) as http:
                r = await http.get(url)
                r.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"kie.ai image download failed: {exc}") from exc
        if not r.content:
            raise ProviderUnavailableError("kie.ai image download returned empty body")
        out_path.write_bytes(r.content)


__all__ = ["KieImageClient", "KieTaskGone", "KieTaskPending", "extract_image_url"]
