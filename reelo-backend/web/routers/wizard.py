"""Wizard endpoints (Module 1: reelo-scriptwriting).

- ``POST /wizard/message`` — Phase A refine chat → ``{reply, outline?}``.
- ``POST /wizard/approve`` — Phase B: build + persist a SeriesSpec shell → ``{series}``.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from clients.base import InvalidKeyError, ProviderUnavailableError
from config import get_settings
from db.repository import UserSettingsRepo
from models.spec import VoiceConfig, VoiceSample
from module1.persistence import save_series_spec
from module1.wizard import build_series_spec, run_phase_a
from web._provider_keys import provider_requires_sample
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

# Dev/test fallback script provider when the account has none configured. In
# prod a missing script provider is a hard 409 (the UI must gate creation on the
# Settings page); in dev we fall back to the keyless stub so local flows + tests
# run without configuration.
_DEV_FALLBACK_SCRIPT = "stub-script"


async def _account_providers(db: DbSession, user_id: str) -> dict[str, str | None]:
    """Read the user's account-level providers, degrading to defaults on error.

    A DB hiccup (or a faked session in tests) must not 500 the wizard: fall back
    to :meth:`UserSettingsRepo.default_providers` so dev/test flows keep working
    (prod gating happens on the resolved values below).
    """
    try:
        return await UserSettingsRepo(db).get_providers(user_id)
    except Exception as exc:  # noqa: BLE001 — settings read is best-effort
        log.warning("account providers read failed (%s); using defaults", exc)
        return UserSettingsRepo.default_providers()


async def _account_voice_sample(db: DbSession, user_id: str) -> dict | None:
    """Read the account-level voice-clone sample, degrading to None on error.

    Best-effort (mirrors :func:`_account_providers`): a DB hiccup or a faked
    session in tests must not 500 the approve flow — a missing sample just means
    the snapshotted clone config carries no ``voice_sample`` (the Settings page
    warns the user; produce will surface the voice error).
    """
    try:
        return await UserSettingsRepo(db).get_voice_sample(user_id)
    except Exception as exc:  # noqa: BLE001 — sample read is best-effort
        log.warning("account voice sample read failed (%s); treating as none", exc)
        return None


def _build_voice_config(
    base: VoiceConfig, provider: str, sample: dict | None
) -> VoiceConfig:
    """Align the request's VoiceConfig with the account voice provider (snapshot).

    For a clone provider (OmniVoice) → ``mode="clone"``, ``voice_id=""``,
    ``settings=None``, and ``voice_sample`` snapshotted from the account sample
    (``None`` when the user has not uploaded one). For any other provider →
    ``mode="preset"`` with the request's voice settings preserved.
    """
    if provider_requires_sample(provider):
        return base.model_copy(
            update={
                "provider": provider,
                "mode": "clone",
                "voice_id": "",
                "settings": None,
                "voice_sample": VoiceSample(**sample) if sample else None,
            }
        )
    return base.model_copy(
        update={"provider": provider, "mode": "preset", "voice_sample": None}
    )


async def _resolve_script_provider(db: DbSession, user_id: str) -> str:
    """The account-level script provider, or a dev fallback / prod 409.

    The provider for scriptwriting comes from the user's account settings (set
    once in the Settings page), NOT the request. When unset: prod raises 409 so
    the UI routes the user to configure it; dev falls back to the keyless stub.
    """
    provider = (await _account_providers(db, user_id)).get("script")
    if provider:
        return provider
    if get_settings().is_prod:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Chưa cấu hình provider viết kịch bản. Vào trang Cấu hình AI để chọn "
                "provider script trước khi tạo series."
            ),
        )
    return _DEV_FALLBACK_SCRIPT


@router.post("/message", response_model=WizardMessageResponse)
async def wizard_message(
    body: WizardMessageRequest, user_id: CurrentUser, db: DbSession
) -> WizardMessageResponse:
    """Phase A refine chat → ``{reply, outline?}`` (maps ``sendWizardMessage``)."""
    # The script provider is account-level (Settings page), never from the body.
    provider = await _resolve_script_provider(db, user_id)
    call_ctx = await build_call_context({}, user_id)
    history = [{"role": m.role, "text": m.text} for m in body.history]
    # Honour Setup skill/language if supplied; provider comes from account settings.
    phase_a_kwargs: dict[str, str] = {"provider": provider}
    if body.skill is not None:
        phase_a_kwargs["skill"] = body.skill
    if body.language is not None:
        phase_a_kwargs["language"] = body.language
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
    """Phase B approve → persist SeriesSpec shell (no AI, D6), return ``{series}``.

    Providers are SNAPSHOTTED from the user's account settings (Settings page),
    not from the request — the wizard config no longer carries provider choices.
    In prod, script + image must be configured (409 otherwise); voice defaults to
    the keyless Edge-TTS. The snapshot freezes the choice into the series so a
    later Settings change does not retroactively alter existing series.
    """
    cfg = body.config
    account = await _account_providers(db, user_id)
    script = account.get("script")
    image = account.get("image")
    voice = account.get("voice") or "edge"

    if get_settings().is_prod and (not script or not image):
        missing = ", ".join(t for t, v in (("script", script), ("image", image)) if not v)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Chưa cấu hình provider ({missing}). Vào trang Cấu hình AI để chọn "
                "provider trước khi tạo series."
            ),
        )

    providers = {
        "script": script or _DEV_FALLBACK_SCRIPT,
        "image": image or "web",
        "voice": voice,
    }
    # Module 2 resolves the TTS provider from ``series.voice.provider`` (not
    # ``providers["voice"]``), so align the VoiceConfig with the account choice.
    # When the account voice provider is OmniVoice, snapshot the account-level
    # voice-clone sample into the series so a later Settings change does not
    # retroactively alter existing series (mode="clone"); other providers keep
    # the request's preset config. Missing sample → clone mode with no sample,
    # which the renderer reports as a voice error (series creation is NOT
    # blocked; the Settings page warns the user up-front).
    voice_cfg = _build_voice_config(cfg.voice, voice, await _account_voice_sample(db, user_id))
    spec = build_series_spec(
        name=body.name,
        topic=body.topic,
        outline=[o.model_dump() for o in body.outline],
        skill=cfg.skill,
        language=cfg.language,
        target_minutes=cfg.target_minutes,
        density=cfg.density,
        providers=providers,
        voice=voice_cfg,
        image_style=cfg.image_style,
    )
    await save_series_spec(db, user_id, spec)
    return WizardApproveResponse(series=spec)


__all__ = ["router"]
