"""Wizard endpoints (Module 1: reelo-scriptwriting).

- ``POST /wizard/message`` — Phase A refine chat → ``{reply, outline?}``.
- ``POST /wizard/approve`` — Phase B: build + persist a SeriesSpec shell → ``{series}``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from clients.base import InvalidKeyError, ProviderUnavailableError
from module1.persistence import save_series_spec
from module1.wizard import build_series_spec, run_phase_a
from web.deps import CurrentUser, DbSession
from web.schemas import (
    OutlineItemModel,
    WizardApproveRequest,
    WizardApproveResponse,
    WizardMessageRequest,
    WizardMessageResponse,
)
from worker.tasks import build_call_context, flush_call_context_usage

log = logging.getLogger("reelo.web.wizard")

router = APIRouter(prefix="/wizard", tags=["wizard"])


@router.post("/message", response_model=WizardMessageResponse)
async def wizard_message(
    body: WizardMessageRequest, user_id: CurrentUser, db: DbSession
) -> WizardMessageResponse:
    """Phase A refine chat → ``{reply, outline?}`` (maps ``sendWizardMessage``)."""
    call_ctx = await build_call_context({}, user_id)
    history = [{"role": m.role, "text": m.text} for m in body.history]
    # Honour Setup selection if supplied; otherwise keep run_phase_a defaults.
    phase_a_kwargs: dict[str, str] = {}
    if body.skill is not None:
        phase_a_kwargs["skill"] = body.skill
    if body.language is not None:
        phase_a_kwargs["language"] = body.language
    if body.provider is not None:
        phase_a_kwargs["provider"] = body.provider
    try:
        result = await run_phase_a(body.idea, history, ctx=call_ctx, **phase_a_kwargs)
    except (ProviderUnavailableError, InvalidKeyError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    finally:
        try:
            await flush_call_context_usage(call_ctx)
        except Exception as exc:  # noqa: BLE001 — usage flush is best-effort
            log.warning("usage flush failed: %s", exc)

    outline = (
        [OutlineItemModel(id=o.id, title=o.title, desc=o.desc, pick=o.pick) for o in result.outline]
        if result.outline is not None
        else None
    )
    return WizardMessageResponse(reply=result.reply, outline=outline)


@router.post("/approve", response_model=WizardApproveResponse)
async def wizard_approve(
    body: WizardApproveRequest, user_id: CurrentUser, db: DbSession
) -> WizardApproveResponse:
    """Phase B approve → persist SeriesSpec shell (no AI, D6), return ``{series}``."""
    cfg = body.config
    spec = build_series_spec(
        name=body.name,
        topic=body.topic,
        outline=[o.model_dump() for o in body.outline],
        skill=cfg.skill,
        language=cfg.language,
        target_minutes=cfg.target_minutes,
        density=cfg.density,
        providers=cfg.providers,
        voice=cfg.voice,
        image_style=cfg.image_style,
    )
    await save_series_spec(db, user_id, spec)
    return WizardApproveResponse(series=spec)


__all__ = ["router"]
