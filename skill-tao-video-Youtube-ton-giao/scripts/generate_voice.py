#!/usr/bin/env python3
"""Generate voice audio from script text via ElevenLabs API.

Reads ELEVENLABS_API_KEY from .env at the skill root.
Writes MP3 to --output, prints metadata JSON to stdout.
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

SKILL_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(SKILL_ROOT / ".env")

API_BASE = "https://api.elevenlabs.io/v1"
MAX_ATTEMPTS = 3
RETRY_WAIT_BASE = 3  # seconds: 3, 6, 9 between attempts


def generate_voice(
    text: str,
    voice_id: str,
    output_path: Path,
    model: str = "eleven_multilingual_v2",
    stability: float = 0.55,
    similarity_boost: float = 0.75,
    style: float = 0.35,
    speed: float = 0.92,
) -> dict:
    api_key = os.environ.get("ELEVENLABS_API_KEY")
    if not api_key:
        raise SystemExit(
            "ELEVENLABS_API_KEY not set. Add it to .env in the skill root.\n"
            f"Expected at: {SKILL_ROOT / '.env'}"
        )

    url = f"{API_BASE}/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    body = {
        "text": text,
        "model_id": model,
        "voice_settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "style": style,
            "speed": speed,
            "use_speaker_boost": True,
        },
    }

    last_err = None
    response = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(url, json=body, headers=headers, timeout=180)
            if response.status_code == 200:
                break
            last_err = f"HTTP {response.status_code}: {response.text[:300]}"
        except requests.RequestException as e:
            last_err = str(e)
            response = None
        if attempt < MAX_ATTEMPTS:
            wait = RETRY_WAIT_BASE * attempt
            print(
                f"ElevenLabs attempt {attempt}/{MAX_ATTEMPTS} failed: {last_err}. "
                f"Retrying in {wait}s...",
                file=sys.stderr,
            )
            time.sleep(wait)
    if response is None or response.status_code != 200:
        raise SystemExit(f"ElevenLabs failed after {MAX_ATTEMPTS} attempts. Last error: {last_err}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)

    return {
        "output_path": str(output_path),
        "voice_id": voice_id,
        "model": model,
        "settings": {
            "stability": stability,
            "similarity_boost": similarity_boost,
            "style": style,
            "speed": speed,
        },
        "character_count": len(text),
        "file_size_bytes": output_path.stat().st_size,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text-file", required=True, help="Path to UTF-8 text file containing the voice script")
    parser.add_argument("--voice-id", required=True, help="ElevenLabs voice ID")
    parser.add_argument("--output", required=True, help="Output MP3 path")
    parser.add_argument("--model", default="eleven_multilingual_v2")
    parser.add_argument("--stability", type=float, default=0.55)
    parser.add_argument("--similarity-boost", type=float, default=0.75)
    parser.add_argument("--style", type=float, default=0.35)
    parser.add_argument("--speed", type=float, default=0.92)
    args = parser.parse_args()

    raw = Path(args.text_file).read_text(encoding="utf-8")
    # Strip image-break markers (lines that are only ===) so the same script.md
    # can be the single source of truth for both voice gen and image timing.
    lines = [ln for ln in raw.split("\n") if ln.strip() != "==="]
    text = "\n".join(lines).strip()
    # Collapse runs of blank lines left behind by stripped markers.
    import re
    text = re.sub(r"\n{3,}", "\n\n", text)
    if not text:
        raise SystemExit(f"Text file is empty: {args.text_file}")

    result = generate_voice(
        text=text,
        voice_id=args.voice_id,
        output_path=Path(args.output),
        model=args.model,
        stability=args.stability,
        similarity_boost=args.similarity_boost,
        style=args.style,
        speed=args.speed,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
