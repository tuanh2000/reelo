"""OmniVoice voice-clone client — talks to the Reelo-hosted GPU microservice.

OmniVoice (k2-fsa) is a zero-shot voice-cloning TTS. Unlike the other voice
providers it does NOT use a fixed ``voice_id``: each call sends a **reference
audio sample + its transcript** plus the target text, and the service returns the
target text spoken in the cloned voice.

The model is GPU-only and ships as a Python library, so Reelo runs it as a
separate microservice (``services/omnivoice/``) and reaches it over HTTP at
``OMNIVOICE_URL``. This client:

- is **keyless from the user's side** (``requires_key = False``): Reelo hosts the
  GPU, so there is no per-user BYOK key. Availability is gated on ``OMNIVOICE_URL``
  being configured (auth ``type: none`` in ``services.yaml``).
- ``cost_tier = "paid"``: cloning runs on Reelo-paid GPU compute (note the
  capability in ``services.yaml``; usage is still recorded in characters for the
  dashboard).
- POSTs multipart to ``{OMNIVOICE_URL}/clone`` → receives ``audio/wav`` 24 kHz →
  transcodes to MP3 with ffmpeg (so the rest of Module 2's concat/render pipeline
  is unchanged — every voice part is an mp3).

Service/network errors surface as :class:`ProviderUnavailableError` so the run
fails cleanly (voice-clone is selected explicitly, never auto-fallback).
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from clients.base import (
    AIClient,
    CallContext,
    ProviderUnavailableError,
    Task,
    VoiceRequest,
    VoiceResult,
)
from module2 import ffmpeg
from usage import compute_cost

# Strip lone "===" image-break lines so the same script feeds voice + image.
from clients.edge_tts import _strip_image_markers


class OmniVoiceClient(AIClient):
    """Zero-shot voice cloning via the OmniVoice microservice."""

    cost_tier = "paid"  # Reelo-hosted GPU compute (not BYOK)
    requires_key = False  # keyless from the user; gated on OMNIVOICE_URL

    # ---- base URL ----------------------------------------------------------
    def _base_url(self) -> str | None:
        """Resolve the service base URL: services.yaml ``endpoint`` or env."""
        endpoint = self.config.endpoint
        if endpoint:
            return endpoint.rstrip("/")
        from config import get_settings

        url = get_settings().omnivoice_url
        return url.rstrip("/") if url else None

    # ---- availability ------------------------------------------------------
    async def is_available(self, ctx: CallContext) -> bool:
        """Available iff a service URL is configured (no per-user key needed).

        We deliberately do NOT block on a live ``/health`` ping here (resolve is
        on the hot path); a down service surfaces as ProviderUnavailableError at
        call time. ``health_ok`` is offered for an explicit pre-flight.
        """
        return self._base_url() is not None

    async def health_ok(self, *, timeout: float = 5.0) -> bool:
        """Optional liveness ping (``GET /health``) — used by setup/validation."""
        import httpx

        base = self._base_url()
        if not base:
            return False
        try:
            async with httpx.AsyncClient(timeout=timeout) as http:
                r = await http.get(f"{base}/health")
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def validate_key(self, ctx: CallContext) -> bool:
        """Keyless: 'valid' == the service is reachable."""
        return await self.health_ok()

    # ---- generate-voice ----------------------------------------------------
    async def generate_voice(
        self, req: VoiceRequest, out_path: Path, ctx: CallContext
    ) -> VoiceResult:
        """Clone ``req.text`` in the voice of ``req.ref_audio`` → MP3 at ``out_path``.

        Sends ref audio + ref transcript + target text (+ optional language) to
        ``/clone``, receives 24 kHz WAV, transcodes to MP3 so the part fits the
        existing concat pipeline. Records usage (characters) for the dashboard.
        """
        import httpx

        base = self._base_url()
        if not base:
            raise ProviderUnavailableError(
                "OmniVoice not configured: set OMNIVOICE_URL to the voice-clone service."
            )

        # Resolve target text.
        if req.text is not None:
            text = req.text
        elif req.text_file is not None:
            text = Path(req.text_file).read_text(encoding="utf-8")
        else:
            raise ProviderUnavailableError("VoiceRequest needs text or text_file")
        text = _strip_image_markers(text)
        if not text.strip():
            raise ProviderUnavailableError("Empty voice text")

        # Reference sample is mandatory for cloning.
        if not req.ref_audio:
            raise ProviderUnavailableError(
                "OmniVoice needs a voice sample (ref_audio). Upload one via "
                "POST /series/{id}/voice-sample."
            )
        ref_path = Path(req.ref_audio)
        if not ref_path.exists():
            raise ProviderUnavailableError(f"ref_audio not found: {ref_path}")
        if not req.ref_text or not req.ref_text.strip():
            raise ProviderUnavailableError("OmniVoice needs ref_text (sample transcript)")

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # POST multipart -> WAV bytes.
        try:
            async with httpx.AsyncClient(timeout=600) as http:
                with ref_path.open("rb") as fh:
                    files = {"ref_audio": (ref_path.name, fh, "audio/wav")}
                    data: dict[str, str] = {"ref_text": req.ref_text, "text": text}
                    if req.language:
                        data["language"] = req.language
                    resp = await http.post(f"{base}/clone", files=files, data=data)
        except httpx.HTTPError as exc:
            raise ProviderUnavailableError(f"OmniVoice request failed: {exc}") from exc

        if resp.status_code >= 400:
            detail = _error_detail(resp)
            raise ProviderUnavailableError(
                f"OmniVoice /clone HTTP {resp.status_code}: {detail}"
            )
        wav_bytes = resp.content
        if not wav_bytes:
            raise ProviderUnavailableError("OmniVoice returned an empty audio body")

        # Transcode the 24 kHz WAV → MP3 so the concat/render pipeline is uniform.
        await _wav_bytes_to_mp3(wav_bytes, out_path)

        chars = len(text)
        cost = compute_cost(Task.GENERATE_VOICE.value, float(chars), self.config.pricing)
        ctx.usage.record(
            ctx.user_id, self.provider_id, Task.GENERATE_VOICE.value, float(chars), cost
        )
        return VoiceResult(out_path=out_path, duration_s=None, chars=chars, raw=None)


def _error_detail(resp) -> str:
    """Best-effort error message from a non-2xx /clone response."""
    try:
        body = resp.json()
        if isinstance(body, dict) and "detail" in body:
            return str(body["detail"])
        return str(body)
    except Exception:  # noqa: BLE001 — non-JSON body
        return resp.text[:200] if resp.text else "(no body)"


async def _wav_bytes_to_mp3(wav_bytes: bytes, out_path: Path) -> Path:
    """Write ``wav_bytes`` to a temp file and transcode to MP3 at ``out_path``.

    Uses the shared ffmpeg seam (``ffmpeg -i in.wav -b:a 192k out.mp3``) so the
    output matches the rest of Module 2's voice parts (192 kbit MP3).
    """
    out_path = Path(out_path)
    fd = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp_wav = Path(fd.name)
    try:
        fd.write(wav_bytes)
        fd.close()
        argv = [
            ffmpeg.ffmpeg_bin(),
            "-y",
            "-i",
            str(tmp_wav),
            "-c:a",
            "libmp3lame",
            "-b:a",
            "192k",
            str(out_path),
        ]
        await ffmpeg.run(argv, timeout=300)
    except ffmpeg.FFmpegError as exc:
        raise ProviderUnavailableError(f"OmniVoice wav→mp3 transcode failed: {exc}") from exc
    finally:
        tmp_wav.unlink(missing_ok=True)
    return out_path


__all__ = ["OmniVoiceClient"]
