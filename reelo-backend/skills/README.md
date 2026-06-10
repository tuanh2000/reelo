# `skills/<id>/template.yaml`

Multi-skill template system (integration §3). Owned by Module 1
(reelo-scriptwriting) for content; the platform-lead keeps the directory layout
and the loader contract.

Each skill lives in `skills/<id>/template.yaml` with `script`, `image`, and
`voice` sections (see integration §3 for the `religion` reference).

A skill controls the **writing style** of the script (structure + narration
rules applied at script generation), NOT the chat. It is never a content gate:
the wizard chat (Phase A) is topic-agnostic. `explain` / `story` / `news` are
general-purpose skills that work for ANY subject; `religion` is a specialised
scholarly skill for religious/historical content with per-tradition image
layers. The default skill is `explain`.

Module 1 should add a `load_skill_template(skill_id)` loader under `module1/`.
