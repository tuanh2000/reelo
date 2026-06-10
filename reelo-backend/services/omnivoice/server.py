"""OmniVoice reference microservice — a thin FastAPI wrapper over the k2-fsa
OmniVoice zero-shot voice-cloning model.

OmniVoice (https://huggingface.co/k2-fsa/OmniVoice) ships only as a **Python
library** — there is no HTTP server. This module is the minimal server Reelo
talks to: load the model once on first use, expose ``POST /clone`` (ref audio +
ref transcript + target text -> cloned-voice WAV 24 kHz), plus ``GET /health``.

Deploy this on a **GPU/CUDA host** (the model needs PyTorch + a CUDA GPU; it does
NOT run on macOS / CPU in any reasonable time). Reelo's backend reaches it via
the ``OMNIVOICE_URL`` env var and never imports torch itself — the heavy model
stays out of the Arq worker.

Run (on the GPU host)::

    pip install -r requirements.txt
    uvicorn server:app --host 0.0.0.0 --port 8002

Smoke-test WITHOUT a GPU (no torch/omnivoice import; returns 24 kHz silence so
the wire contract and Reelo's client can be exercised end-to-end)::

    OMNIVOICE_MOCK=1 uvicorn server:app --port 8002

The mock path is import-clean on any platform: ``torch`` / ``omnivoice`` are
imported lazily inside the real generate path, *after* the mock flag is checked.
"""

from __future__ import annotations

import io
import logging
import os
import struct
import tempfile
import wave
from pathlib import Path

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import Response

log = logging.getLogger("omnivoice.server")

# Model identifiers / runtime knobs (all overridable via env on the GPU host).
MODEL_ID = os.environ.get("OMNIVOICE_MODEL", "k2-fsa/OmniVoice")
DEVICE = os.environ.get("OMNIVOICE_DEVICE", "cuda:0")
SAMPLE_RATE = int(os.environ.get("OMNIVOICE_SAMPLE_RATE", "24000"))


def _mock_enabled() -> bool:
    """True when ``OMNIVOICE_MOCK`` is set — return silence, never load the model.

    Read at request time (not import time) so tests can toggle it per-call.
    """
    return os.environ.get("OMNIVOICE_MOCK", "").strip() not in ("", "0", "false", "False")


app = FastAPI(title="OmniVoice service", version="1.0.0")

# Process-wide singleton; lazily built on the first real /clone call so importing
# this module (and `OMNIVOICE_MOCK=1` smoke tests) never touches torch.
_MODEL = None


def _get_model():
    """Load (once) and return the OmniVoice model. Real GPU path only.

    Raises:
        HTTPException(503): if torch/omnivoice cannot be imported or the model
            fails to load (no GPU, missing weights, …).
    """
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        import torch  # noqa: PLC0415 — heavy import deferred behind the mock flag
        from omnivoice import OmniVoice  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503,
            detail=(
                "OmniVoice runtime unavailable: could not import torch/omnivoice. "
                "This service requires a CUDA GPU host. "
                f"({type(exc).__name__}: {exc})"
            ),
        ) from exc
    try:
        _MODEL = OmniVoice.from_pretrained(
            MODEL_ID, device_map=DEVICE, dtype=torch.float16
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=503, detail=f"OmniVoice model failed to load: {exc}"
        ) from exc
    log.info("OmniVoice model loaded: %s on %s", MODEL_ID, DEVICE)
    return _MODEL


@app.get("/health")
async def health() -> dict[str, object]:
    """Liveness + mode. ``mock`` is True when serving silence (no model)."""
    return {
        "status": "ok",
        "model": MODEL_ID,
        "device": DEVICE,
        "sample_rate": SAMPLE_RATE,
        "mock": _mock_enabled(),
        "loaded": _MODEL is not None,
    }


@app.post("/clone")
async def clone(
    ref_audio: UploadFile = File(..., description="Reference voice sample (wav)"),
    ref_text: str = Form(..., description="Exact transcript of the reference audio"),
    text: str = Form(..., description="Target text to speak in the cloned voice"),
    language: str | None = Form(default=None, description="Optional language code/name"),
) -> Response:
    """Synthesize ``text`` in the voice of ``ref_audio`` -> ``audio/wav`` (24 kHz).

    Multipart form: ``ref_audio`` (wav file) + ``ref_text`` + ``text`` (+ optional
    ``language``). Returns raw WAV bytes (mono, ``SAMPLE_RATE`` Hz). On the mock
    path it returns a short silence clip so the full wire path is exercisable
    without a GPU.
    """
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="empty `text`")
    if not ref_text or not ref_text.strip():
        raise HTTPException(status_code=400, detail="empty `ref_text`")

    ref_bytes = await ref_audio.read()
    if not ref_bytes:
        raise HTTPException(status_code=400, detail="empty `ref_audio` upload")

    if _mock_enabled():
        # ~1s of silence scaled loosely to text length; enough for a valid WAV.
        seconds = max(0.5, min(10.0, len(text) / 15.0))
        return Response(content=_silence_wav(seconds), media_type="audio/wav")

    return await _generate_real(ref_bytes, ref_text, text, language)


async def _generate_real(
    ref_bytes: bytes, ref_text: str, text: str, language: str | None
) -> Response:
    """Real GPU generation path. Writes the ref wav to a temp file, runs the
    model, encodes the np.ndarray output back to WAV bytes."""
    import soundfile as sf  # noqa: PLC0415 — only needed on the real path

    model = _get_model()
    tmp_dir = Path(tempfile.mkdtemp(prefix="omnivoice_"))
    ref_path = tmp_dir / "ref.wav"
    out_path = tmp_dir / "out.wav"
    try:
        ref_path.write_bytes(ref_bytes)
        kwargs: dict[str, object] = {
            "text": text,
            "ref_audio": str(ref_path),
            "ref_text": ref_text,
        }
        if language:
            kwargs["language"] = language
        try:
            audio = model.generate(**kwargs)
        except TypeError:
            # Older signature without `language`.
            kwargs.pop("language", None)
            audio = model.generate(**kwargs)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(
                status_code=500, detail=f"OmniVoice generation failed: {exc}"
            ) from exc
        # Output is list[np.ndarray] @ 24 kHz; take the first item.
        wav = audio[0] if isinstance(audio, (list, tuple)) else audio
        sf.write(str(out_path), wav, SAMPLE_RATE)
        return Response(content=out_path.read_bytes(), media_type="audio/wav")
    finally:
        for p in (ref_path, out_path):
            p.unlink(missing_ok=True)
        try:
            tmp_dir.rmdir()
        except OSError:
            pass


def _silence_wav(seconds: float, *, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Build a valid mono 16-bit PCM WAV of ``seconds`` of silence (mock path).

    Uses only the stdlib ``wave`` module so the mock path needs no numpy/torch.
    """
    n = int(seconds * sample_rate)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)  # 16-bit
        w.setframerate(sample_rate)
        w.writeframes(struct.pack("<%dh" % n, *([0] * n)))
    return buf.getvalue()
