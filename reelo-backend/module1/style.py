"""Image-style resolution + reference-image style inference (module-1 §10, D4).

Two orthogonal sources combine into the final visual style:
- **preset** (visual) — ``styles/presets.yaml``: a ``base_prompt`` + palette +
  description per UI preset (cinematic / documentary / … / painterly-devotional).
- **skill template** (context) — an optional per-tradition ``style_layer``.

:func:`resolve_image_style` builds a fully populated :class:`models.spec.ImageStyle`
from a preset id + chosen skill/tradition/aspect (Phase B approve, §6).

:func:`infer_style` is the ``POST /style/infer`` backend. v1 is a heuristic stub
(palette extracted from the uploaded PNG/JPEG bytes when possible, else a default
palette) returning ``{palette, description}`` — no vision API call. See report.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from models.spec import ImageStyle

from module1.skills import load_skill_template

STYLES_YAML = Path(__file__).resolve().parent.parent / "styles" / "presets.yaml"

# Fallback when /style/infer cannot extract anything from the bytes.
_DEFAULT_PALETTE = ["#1f2937", "#c89b3c", "#f5f0e6"]
_DEFAULT_DESCRIPTION = "Warm, reverent tones with soft natural light."


class PresetError(Exception):
    """The requested preset id is unknown / presets.yaml is malformed."""


@dataclass(frozen=True)
class Preset:
    """One entry from ``styles/presets.yaml``."""

    preset_id: str
    base_prompt: str
    palette: list[str]
    description: str


def _styles_path() -> Path:
    return Path(os.environ.get("REELO_STYLES_YAML") or STYLES_YAML)


@lru_cache(maxsize=1)
def _load_presets() -> dict[str, Preset]:
    path = _styles_path()
    if not path.is_file():
        raise PresetError(f"presets file not found at {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw = data.get("presets", {}) or {}
    out: dict[str, Preset] = {}
    for pid, entry in raw.items():
        entry = entry or {}
        out[pid] = Preset(
            preset_id=pid,
            base_prompt=str(entry.get("base_prompt") or "").strip(),
            palette=list(entry.get("palette") or []),
            description=str(entry.get("description") or "").strip(),
        )
    return out


def get_preset(preset_id: str) -> Preset:
    presets = _load_presets()
    if preset_id not in presets:
        raise PresetError(f"unknown preset id: {preset_id!r} (have: {sorted(presets)})")
    return presets[preset_id]


def list_presets() -> list[Preset]:
    return list(_load_presets().values())


def resolve_image_style(
    *,
    preset_id: str,
    skill: str,
    aspect: str = "16:9",
    tradition: str | None = None,
    palette: list[str] | None = None,
    description: str | None = None,
) -> ImageStyle:
    """Resolve a full :class:`ImageStyle` = preset.base_prompt + skill style_layer (D4).

    ``palette`` / ``description`` override the preset's defaults when supplied
    (e.g. values produced by :func:`infer_style`).
    """
    preset = get_preset(preset_id)
    style_layer: str | None = None
    try:
        tmpl = load_skill_template(skill)
        style_layer = tmpl.style_layer_for(tradition)
    except Exception:  # noqa: BLE001 — a missing skill template is non-fatal here
        style_layer = None
    return ImageStyle(
        preset_id=preset_id,
        base_prompt=preset.base_prompt,
        palette=palette if palette is not None else list(preset.palette),
        description=description if description is not None else preset.description,
        aspect=aspect,  # type: ignore[arg-type]  # validated by the SeriesConfig boundary
        style_layer=style_layer,
    )


# --------------------------------------------------------------------------- #
# inferStyle (POST /style/infer) — heuristic v1                               #
# --------------------------------------------------------------------------- #
def _png_dominant_color(data: bytes) -> str | None:
    """Return a hex colour from a PNG by averaging a few IDAT-decoded pixels.

    Best-effort and stdlib-only (zlib). Returns ``None`` on any decode trouble.
    """
    import zlib

    if data[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    try:
        width, height = struct.unpack(">II", data[16:24])
        bit_depth = data[24]
        color_type = data[25]
        if bit_depth != 8 or color_type not in (2, 6):  # 8-bit RGB / RGBA only
            return None
        channels = 3 if color_type == 2 else 4
        # Concatenate all IDAT chunks.
        idat = bytearray()
        pos = 8
        while pos + 8 <= len(data):
            length = struct.unpack(">I", data[pos : pos + 4])[0]
            tag = data[pos + 4 : pos + 8]
            chunk = data[pos + 8 : pos + 8 + length]
            if tag == b"IDAT":
                idat += chunk
            pos += 12 + length
            if tag == b"IEND":
                break
        raw = zlib.decompress(bytes(idat))
        stride = width * channels + 1  # +1 filter byte per row
        rs = gs = bs = cnt = 0
        for y in range(height):
            row_start = y * stride + 1  # skip filter byte (assume filter 0)
            for x in range(0, min(width, 8)):  # sample up to 8 px/row
                p = row_start + x * channels
                if p + 2 >= len(raw):
                    break
                rs += raw[p]
                gs += raw[p + 1]
                bs += raw[p + 2]
                cnt += 1
        if cnt == 0:
            return None
        return f"#{rs // cnt:02x}{gs // cnt:02x}{bs // cnt:02x}"
    except Exception:  # noqa: BLE001
        return None


def infer_style(images: list[bytes]) -> dict[str, Any]:
    """Infer ``{palette, description}`` from uploaded reference image bytes (§10).

    v1 heuristic: extract a dominant colour per PNG reference (stdlib only) to
    seed the palette; fall back to a default palette when nothing decodes. No
    external vision call in v1 (see report — candidate for a 1-call upgrade).
    """
    palette: list[str] = []
    for blob in images:
        hexc = _png_dominant_color(blob)
        if hexc and hexc not in palette:
            palette.append(hexc)
    if not palette:
        palette = list(_DEFAULT_PALETTE)
        description = _DEFAULT_DESCRIPTION
    else:
        description = (
            f"Inferred from {len(images)} reference image(s): a palette anchored on "
            f"{palette[0]} with a coherent, cohesive look."
        )
    return {"palette": palette[:5], "description": description}


__all__ = [
    "STYLES_YAML",
    "PresetError",
    "Preset",
    "get_preset",
    "list_presets",
    "resolve_image_style",
    "infer_style",
]
