"""Module 2 — Video Generator (reelo-video-generator).

Turns a scripted :class:`models.spec.SeriesSpec` / :class:`EpisodeSpec` into a
finished ``final.mp4`` (+ ``subs.srt`` + thumbnail candidates), then uploads the
whole project folder to object storage.

Sub-modules:
- :mod:`module2.ffmpeg` — shared ffmpeg/ffprobe seams (run + build-command split).
- :mod:`module2.materialize` — spec → local project folder (script.md, prompts).
- :mod:`module2.voice` — chunked TTS + concat → ``voice/voice.mp3``.
- :mod:`module2.render` — Reelo-native renderer (Ken Burns + aspect + ducking).
- :mod:`module2.subtitles` — auto-timed SRT.
- :mod:`module2.thumbnail` — 3 AI thumbnail candidates.
- :mod:`module2.timing` — word-count → per-image durations (inherited algorithm).
- :mod:`module2.runner` — ``produce_episode`` orchestration + cost estimate.

Public worker entrypoint: :func:`module2.runner.run_produce_episode`.
"""

from __future__ import annotations
