"""Materializer — :class:`SeriesSpec` / :class:`EpisodeSpec` → a project folder.

The project folder (built in worker local temp, then uploaded to object storage)
is the source of truth for a produce run (Module 2 §3)::

    <root>/
    ├── script.md                 # narration join "\\n\\n===\\n\\n"
    ├── images/  NN_<label>.txt    # composed image prompts (PNGs land here later)
    ├── voice/                     # voice_part_NN.mp3 + voice.mp3 (filled by voice.py)
    ├── music/   bg.mp3            # copied from user upload, if any
    └── thumbnails/                # thumb_1..3.png (filled by thumbnail.py)

**Hard invariant** (integration §8.2): ``count(===) + 1 == len(segments) ==
count(images/*.txt)``; file names are zero-padded ``NN_`` matching ``segment.index``
so segment order == display order. :func:`verify_invariant` enforces it before
render (a missing/extra PNG must block the join, M2-7).

Image-prompt composition (§3, D4): each ``images/NN_<label>.txt`` is
``preset.base_prompt`` + ``image_style.style_layer`` + ``segment.image_prompt``.
``image_style.base_prompt`` (preset) and ``image_style.style_layer`` (skill
template) are already resolved by Module 1 — we just join them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from models.spec import EpisodeSpec, SeriesSpec

SECTION_SEP = "\n\n===\n\n"
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(text: str, *, fallback: str = "scene") -> str:
    """Lowercase ASCII slug for file names (collapses non-alphanumerics to ``_``)."""
    slug = _SLUG_RE.sub("_", text.strip().lower()).strip("_")
    return slug or fallback


def deslug(text: str) -> str:
    """Inverse of :func:`slugify` for search: ``red_knot_bird`` -> ``red knot bird``.

    Drops a leading zero-padded index prefix (``06_``) and turns separators into
    spaces. Used as the web-photo search-query fallback when a segment has no
    ``image_query`` (Module 2 §4.3).
    """
    text = re.sub(r"^\d+[_-]", "", text or "")
    words = [w for w in _SLUG_RE.sub(" ", text.strip().lower()).split() if w]
    return " ".join(words)


def compose_image_prompt(series: SeriesSpec, segment_prompt: str) -> str:
    """Compose the full image prompt: base_prompt + style_layer + segment prompt (§3)."""
    style = series.image_style
    parts = [style.base_prompt, style.style_layer, segment_prompt]
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def image_filename(index: int, label: str) -> str:
    """Zero-padded ``NN_<slug>`` stem (no extension) for a segment's image."""
    return f"{index:02d}_{slugify(label)}"


@dataclass(frozen=True)
class ProjectLayout:
    """Resolved paths inside a materialized project folder."""

    root: Path
    script_md: Path
    images_dir: Path
    voice_dir: Path
    music_dir: Path
    thumbnails_dir: Path

    @property
    def voice_mp3(self) -> Path:
        return self.voice_dir / "voice.mp3"

    @property
    def music_bg(self) -> Path:
        return self.music_dir / "bg.mp3"

    @property
    def subs_srt(self) -> Path:
        return self.root / "subs.srt"

    @property
    def final_mp4(self) -> Path:
        return self.root / "final.mp4"

    def image_png(self, index: int, label: str) -> Path:
        return self.images_dir / f"{image_filename(index, label)}.png"

    def image_txt(self, index: int, label: str) -> Path:
        return self.images_dir / f"{image_filename(index, label)}.txt"

    def media_mp4(self, index: int, label: str) -> Path:
        """Path for a segment's chosen **video clip** (M2-13), under ``images/``.

        Kept beside the image in the project folder so the whole folder still
        uploads as one unit; ``.mp4`` distinguishes it from the still-image path.
        """
        return self.images_dir / f"{image_filename(index, label)}.mp4"


def layout_for(root: Path) -> ProjectLayout:
    """Build a :class:`ProjectLayout` for ``root`` (no filesystem side effects)."""
    root = Path(root)
    return ProjectLayout(
        root=root,
        script_md=root / "script.md",
        images_dir=root / "images",
        voice_dir=root / "voice",
        music_dir=root / "music",
        thumbnails_dir=root / "thumbnails",
    )


def materialize(
    series: SeriesSpec,
    ep: EpisodeSpec,
    root: Path,
    *,
    music_src: Path | None = None,
) -> ProjectLayout:
    """Write ``script.md`` + ``images/NN_<label>.txt`` (+ ``music/bg.mp3``) under ``root``.

    Args:
        series: the parent series (image_style, music).
        ep: the **scripted** episode (must have ``segments``).
        root: project folder (created if missing).
        music_src: optional local path to background music to copy into
            ``music/bg.mp3`` (the worker downloads ``series.music.path`` first).

    Returns:
        The :class:`ProjectLayout` for the folder.

    Raises:
        ValueError: if the episode has no segments (not scripted).
    """
    if not ep.segments:
        raise ValueError(f"episode {ep.episode_id} has no segments — script it first")

    lo = layout_for(root)
    for d in (lo.root, lo.images_dir, lo.voice_dir, lo.music_dir, lo.thumbnails_dir):
        d.mkdir(parents=True, exist_ok=True)

    # script.md — one narration block per segment, joined by the === marker.
    narrations = [s.narration.strip() for s in ep.segments]
    lo.script_md.write_text(SECTION_SEP.join(narrations), encoding="utf-8")

    # images/NN_<label>.txt — composed prompt per segment.
    for s in ep.segments:
        prompt = compose_image_prompt(series, s.image_prompt)
        lo.image_txt(s.index, s.image_label).write_text(prompt, encoding="utf-8")

    # music/bg.mp3 — copy the user-uploaded track if present.
    if music_src is not None:
        src = Path(music_src)
        if src.exists():
            lo.music_bg.write_bytes(src.read_bytes())

    return lo


def expected_image_paths(ep: EpisodeSpec, lo: ProjectLayout) -> list[Path]:
    """The PNG paths every segment must produce, in segment order."""
    return [lo.image_png(s.index, s.image_label) for s in ep.segments]


def count_sections(script_md: Path) -> int:
    """Number of narration blocks in ``script.md`` (``count(===) + 1``)."""
    text = script_md.read_text(encoding="utf-8")
    # Robust to the exact separator: count lines that are only "===".
    seps = sum(1 for line in text.split("\n") if line.strip() == "===")
    return seps + 1 if text.strip() else 0


def verify_invariant(ep: EpisodeSpec, lo: ProjectLayout) -> None:
    """Enforce ``count(===)+1 == len(segments) == count(PNGs)`` before render.

    Raises:
        MaterializeInvariantError: if any count diverges or a PNG is missing.
    """
    n_seg = len(ep.segments)
    n_sec = count_sections(lo.script_md)
    pngs = expected_image_paths(ep, lo)
    present = [p for p in pngs if p.exists()]
    if n_sec != n_seg:
        raise MaterializeInvariantError(
            f"script.md has {n_sec} sections but episode has {n_seg} segments"
        )
    if len(present) != n_seg:
        missing = [p.name for p in pngs if not p.exists()]
        raise MaterializeInvariantError(
            f"expected {n_seg} images, found {len(present)} (missing: {missing})"
        )


class MaterializeInvariantError(RuntimeError):
    """The materialized folder violates the segment/section/image count invariant."""


__all__ = [
    "SECTION_SEP",
    "slugify",
    "deslug",
    "compose_image_prompt",
    "image_filename",
    "ProjectLayout",
    "layout_for",
    "materialize",
    "expected_image_paths",
    "count_sections",
    "verify_invariant",
    "MaterializeInvariantError",
]
