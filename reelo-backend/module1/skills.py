"""Skill template loader (``skills/<id>/template.yaml`` — integration §3).

A skill template is the per-niche knowledge Module 1 uses to drive script
structure and the per-tradition image style layer. ``religion`` is the
reference; ``story`` / ``explain`` / ``news`` are scaffolds.

Typed view (:class:`SkillTemplate`) so callers get attribute access + sane
defaults instead of raw-dict spelunking. Loaded results are cached per
``skill_id`` (templates are static content shipped with the app).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

# ``reelo-backend/skills`` — override for tests/alt catalogs.
SKILLS_ROOT = Path(__file__).resolve().parent.parent / "skills"

# Default structure used when a scaffold skill leaves ``script.structure`` empty.
_DEFAULT_STRUCTURE = ["hook", "body", "closing"]
_DEFAULT_WORD_RATIOS = {"hook": 0.1, "body": 0.78, "closing": 0.12}


class SkillTemplateError(Exception):
    """The requested skill template is missing or malformed."""


@dataclass(frozen=True)
class ScriptTemplate:
    """``script:`` block — structure + word distribution + extra system prompt."""

    structure: list[str]
    word_ratios: dict[str, float]
    rule_prompt_extra: str = ""


@dataclass(frozen=True)
class ImageTemplate:
    """``image:`` block — preset hint + per-tradition style layers + aspect."""

    recommended_preset: str | None = None
    style_layers: dict[str, str] = field(default_factory=dict)
    default_aspect: str = "16:9"


@dataclass(frozen=True)
class VoiceTemplate:
    """``voice:`` block — default TTS voice + settings."""

    default_voice_id: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillTemplate:
    """Typed view over one ``skills/<id>/template.yaml``."""

    id: str
    display_name: str
    script: ScriptTemplate
    image: ImageTemplate
    voice: VoiceTemplate
    raw: dict[str, Any] = field(default_factory=dict)

    def style_layer_for(self, tradition: str | None) -> str | None:
        """Return the per-tradition image style layer, if the skill defines one."""
        if not tradition:
            return None
        return self.image.style_layers.get(tradition)


def _skills_root() -> Path:
    return Path(os.environ.get("REELO_SKILLS_ROOT") or SKILLS_ROOT)


def _coerce(skill_id: str, data: dict[str, Any]) -> SkillTemplate:
    script_raw = data.get("script", {}) or {}
    structure = list(script_raw.get("structure") or []) or list(_DEFAULT_STRUCTURE)
    word_ratios = dict(script_raw.get("word_ratios") or {}) or dict(_DEFAULT_WORD_RATIOS)
    image_raw = data.get("image", {}) or {}
    voice_raw = data.get("voice", {}) or {}
    return SkillTemplate(
        id=data.get("id", skill_id),
        display_name=data.get("display_name", skill_id),
        script=ScriptTemplate(
            structure=structure,
            word_ratios=word_ratios,
            rule_prompt_extra=str(script_raw.get("rule_prompt_extra") or "").strip(),
        ),
        image=ImageTemplate(
            recommended_preset=image_raw.get("recommended_preset"),
            style_layers=dict(image_raw.get("style_layers") or {}),
            default_aspect=str(image_raw.get("default_aspect") or "16:9"),
        ),
        voice=VoiceTemplate(
            default_voice_id=voice_raw.get("default_voice_id"),
            settings=dict(voice_raw.get("settings") or {}),
        ),
        raw=data,
    )


@lru_cache(maxsize=16)
def load_skill_template(skill_id: str) -> SkillTemplate:
    """Load and cache ``skills/<skill_id>/template.yaml`` as a :class:`SkillTemplate`.

    Raises:
        SkillTemplateError: if the file is missing or not a YAML mapping.
    """
    path = _skills_root() / skill_id / "template.yaml"
    if not path.is_file():
        raise SkillTemplateError(f"no template for skill {skill_id!r} at {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as exc:
        raise SkillTemplateError(f"malformed template for {skill_id!r}: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillTemplateError(f"template for {skill_id!r} is not a mapping")
    return _coerce(skill_id, data)


__all__ = [
    "SKILLS_ROOT",
    "SkillTemplateError",
    "ScriptTemplate",
    "ImageTemplate",
    "VoiceTemplate",
    "SkillTemplate",
    "load_skill_template",
]
