# `skills/<id>/template.yaml`

Multi-skill template system (integration §3). Owned by Module 1
(reelo-scriptwriting) for content; the platform-lead keeps the directory layout
and the loader contract.

Each skill lives in `skills/<id>/template.yaml` with `script`, `image`, and
`voice` sections (see integration §3 for the `religion` reference). `religion`
is the reference; `story` / `explain` / `news` are scaffolds Module 1 fills in.

Module 1 should add a `load_skill_template(skill_id)` loader under `module1/`.
