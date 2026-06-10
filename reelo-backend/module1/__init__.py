"""Module 1 — AI Chatting / scriptwriting.

Owns the wizard (Phase A refine, Phase B approve), the lazy per-episode script
generation (RULE + structured-output + parse + validate, chunked), image-style
resolution, and the skill template + preset content.

Public entrypoints used elsewhere:
- :func:`module1.episode_script.generate_episode_script` — the lazy script path,
  wired into ``worker.tasks.generate_script``.
- :func:`module1.wizard.run_phase_a` / :func:`module1.wizard.build_series_spec` —
  the wizard message + approve logic the routers call.
- :func:`module1.style.resolve_image_style` / :func:`module1.style.infer_style`.

The cross-module contract (the ``SeriesSpec`` family) lives in ``models.spec``
and is *not* redefined here; this module only fills it in.
"""

from __future__ import annotations
