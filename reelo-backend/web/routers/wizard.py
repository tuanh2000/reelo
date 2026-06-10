"""Wizard endpoints (Module 1: reelo-scriptwriting).

- ``POST /wizard/message`` — Phase A refine chat → ``{reply, outline?}``.
- ``POST /wizard/approve`` — Phase B: build + persist a SeriesSpec shell → ``{series}``.

Provider selection is PER-SERIES: the user picks the script/image/voice toolset
in the create flow and it is set straight into the new series. API keys remain
PER-USER (entered once in "Cấu hình AI", reused across every series).
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from clients.base import InvalidKeyError, ProviderUnavailableError
from config import get_settings
from db.repository import ApiKeyRepo
from models.spec import VoiceConfig
from module1.persistence import save_series_spec
from module1.wizard import build_series_spec, run_phase_a
from web._provider_keys import (
    first_ready_script_provider,
    provider_requires_sample,
)
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

# Per-series default toolset when the request omits providers (older clients).
# Keyless picks so the create flow never hard-fails; the readiness gate (per-
# series key check) still applies before chat/produce.
_DEFAULT_PROVIDERS: dict[str, str] = {"script": "stub-script", "image": "web", "voice": "edge"}
# Dev/test fallback script provider when the user has no key-ready provider.
_DEV_FALLBACK_SCRIPT = "stub-script"


async def _present_key_refs(db: DbSession, user_id: str) -> set[str]:
    """The set of key_refs the user has saved, degrading to empty on error.

    Best-effort: a DB hiccup (or a faked session in tests) must not 500 the
    wizard — an empty set just means no keyed provider qualifies, so the fallback
    / prod gate kicks in.
    """
    try:
        rows = await ApiKeyRepo(db).list_refs(user_id)
        return {row.key_ref for row in rows}
    except Exception as exc:  # noqa: BLE001 — key read is best-effort
        log.warning("api-key refs read failed (%s); treating as none", exc)
        return set()


def _build_voice_config(base: VoiceConfig, provider: str) -> VoiceConfig:
    """Align the request's VoiceConfig with the chosen per-series voice provider.

    For a clone provider (OmniVoice) → ``mode="clone"``, ``voice_id=""``,
    ``settings=None``, no sample yet (uploaded per-series after approve via
    ``POST /series/{id}/voice-sample``). For any other provider → ``mode="preset"``
    keeping the request's voice settings.
    """
    if provider_requires_sample(provider):
        return base.model_copy(
            update={
                "provider": provider,
                "mode": "clone",
                "voice_id": "",
                "settings": None,
                "voice_sample": None,
            }
        )
    return base.model_copy(
        update={"provider": provider, "mode": "preset", "voice_sample": None}
    )


async def _resolve_script_provider(
    db: DbSession, user_id: str, requested: str | None
) -> str:
    """The per-series script provider to use for Phase A chat.

    Prefers the provider the user picked for the series (``requested``). When the
    chat runs before that choice exists (start of the create flow), fall back to
    the first key-ready script provider for the user. When nothing qualifies:
    prod raises 409 (the UI routes the user to add a key); dev uses the keyless
    stub so local flows + tests work without configuration.
    """
    if requested:
        return requested
    present = await _present_key_refs(db, user_id)
    provider = first_ready_script_provider(present)
    if provider:
        return provider
    if get_settings().is_prod:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "Chưa có key cho provider viết kịch bản nào. Vào trang Cấu hình AI "
                "để thêm key, hoặc chọn provider cho series này trước khi chat."
            ),
        )
    return _DEV_FALLBACK_SCRIPT


@router.post("/message", response_model=WizardMessageResponse)
async def wizard_message(
    body: WizardMessageRequest, user_id: CurrentUser, db: DbSession
) -> WizardMessageResponse:
    """Phase A refine chat → ``{reply, outline?}`` (maps ``sendWizardMessage``).

    Uses the per-series script provider the user picked (``body.provider``); when
    absent it falls back to the user's first key-ready script provider.
    """
    provider = await _resolve_script_provider(db, user_id, body.provider)
    call_ctx = await build_call_context({}, user_id)
    history = [{"role": m.role, "text": m.text} for m in body.history]
    # Honour Setup skill/language if supplied; provider is per-series.
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

    Providers are PER-SERIES: taken straight from the request config (the toolset
    the user picked in the create flow) and written into ``SeriesSpec.providers``
    + ``SeriesSpec.voice.provider``. When the voice provider is OmniVoice clone,
    the series voice config is set to clone mode WITHOUT a sample — the sample is
    uploaded per-series afterwards (``POST /series/{id}/voice-sample``). Approve
    never blocks on missing keys/samples (the readiness gate handles that before
    chat/produce); older clients that omit providers get keyless defaults.
    """
    cfg = body.config
    chosen = dict(cfg.providers or {})
    providers = {
        "script": chosen.get("script") or _DEFAULT_PROVIDERS["script"],
        "image": chosen.get("image") or _DEFAULT_PROVIDERS["image"],
        "voice": chosen.get("voice") or _DEFAULT_PROVIDERS["voice"],
    }
    # Module 2 resolves the TTS provider from ``series.voice.provider`` (not
    # ``providers["voice"]``), so align the VoiceConfig with the chosen voice.
    voice_cfg = _build_voice_config(cfg.voice, providers["voice"])
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
