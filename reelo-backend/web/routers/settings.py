"""Account-level settings (provider configuration).

The user configures the AI providers for the three generation tasks
(script / image / voice) ONCE, here, before creating any series. A single
provider set is shared across every series the user owns (decision: account-
level config). Series creation gates on this being ready (script + image
configured), so the UI can route the user here when it is not.

- ``GET /settings/providers``  → chosen providers + per-task readiness + catalog.
- ``PUT /settings/providers``  → upsert the chosen providers (partial).

Key storage stays in ``POST /keys`` (per-provider, encrypted per-user); this
router only records *which* provider the user picked and reports whether its
key (if any) is present.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from db.repository import ApiKeyRepo, UserSettingsRepo
from web._provider_keys import (
    provider_requires_key,
    provider_supports_field,
    resolve_key_ref,
)
from web.deps import CurrentUser, DbSession
from web.routers.providers import build_provider_catalog
from web.schemas import (
    ProviderSettingsItem,
    ProviderSettingsResponse,
    SaveProviderSettingsRequest,
)

router = APIRouter(prefix="/settings", tags=["settings"])


def _present_key_refs(rows) -> set[str]:
    return {row.key_ref for row in rows}


def _item_for(provider: str | None, present_refs: set[str]) -> ProviderSettingsItem:
    """Build the per-task readiness item for a chosen provider.

    ``ready`` = a provider is chosen AND (it needs no key OR a key is present).
    The aggregate web image alias ``"web"`` is keyless (web-commons), so it is
    always ready once chosen.
    """
    if not provider:
        return ProviderSettingsItem(provider=None, requires_key=False, has_key=False, ready=False)
    requires_key = provider_requires_key(provider)
    has_key = (resolve_key_ref(provider) in present_refs) if requires_key else False
    ready = (not requires_key) or has_key
    return ProviderSettingsItem(
        provider=provider, requires_key=requires_key, has_key=has_key, ready=ready
    )


@router.get("/providers", response_model=ProviderSettingsResponse)
async def get_provider_settings(
    user_id: CurrentUser, db: DbSession
) -> ProviderSettingsResponse:
    """Return the user's chosen providers + readiness + the per-task catalog."""
    providers = await UserSettingsRepo(db).get_providers(user_id)
    present = _present_key_refs(await ApiKeyRepo(db).list_refs(user_id))

    script = _item_for(providers.get("script"), present)
    image = _item_for(providers.get("image"), present)
    voice = _item_for(providers.get("voice"), present)

    return ProviderSettingsResponse(
        script=script,
        image=image,
        voice=voice,
        script_ready=script.ready,
        image_ready=image.ready,
        voice_ready=voice.ready,
        options=build_provider_catalog(),
    )


@router.put("/providers", response_model=ProviderSettingsResponse)
async def put_provider_settings(
    body: SaveProviderSettingsRequest, user_id: CurrentUser, db: DbSession
) -> ProviderSettingsResponse:
    """Upsert the chosen providers (partial). Validates id supports its task."""
    updates: dict[str, str | None] = {}
    for field in ("script", "image", "voice"):
        value = getattr(body, field)
        if value is None:
            continue  # field omitted (or explicitly cleared via empty handled below)
        value = value.strip()
        if value == "":
            updates[field] = None  # clear the choice
            continue
        if not provider_supports_field(value, field):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Provider '{value}' does not support task '{field}'.",
            )
        updates[field] = value

    if updates:
        await UserSettingsRepo(db).set_providers(user_id, updates)

    return await get_provider_settings(user_id, db)


__all__ = ["router"]
