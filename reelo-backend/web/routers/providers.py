"""Providers catalog (Module 3: reelo-ai-services).

``GET /providers`` derives from services.yaml (per task, with cost_tier /
requires_key / key_help_url). Stub providers (``stub-*``) are excluded from the
catalog the UI sees. Requires an authenticated user (the UI calls it post-login).
"""

from __future__ import annotations

from fastapi import APIRouter

from clients.base import Task
from clients.registry import get_registry
from web.deps import CurrentUser
from web.schemas import ProviderOption, ProvidersResponse

router = APIRouter(prefix="/providers", tags=["providers"])

_TASK_TO_FIELD = {
    Task.WRITE_SCRIPT: "script",
    Task.GENERATE_IMAGE: "image",
    Task.GENERATE_VOICE: "voice",
}


# Per-provider UI hints (note) the YAML does not carry as a field.
_PROVIDER_NOTES = {
    "omnivoice": "Giọng clone — cần tải lên âm thanh mẫu + transcript (không cần key)",
    "claude-cli": (
        "BYO tài khoản Claude — dán OAuth token từ `claude setup-token` "
        "(dùng subscription của chính bạn, không cần API key trả theo token)"
    ),
}


def _provider_option(provider_id: str, raw: dict) -> ProviderOption:
    auth = raw.get("auth", {}) or {}
    requires_key = auth.get("type", "key") != "none"
    return ProviderOption(
        id=provider_id,
        name=raw.get("display_name", provider_id),
        cost_tier=raw.get("cost_tier", "paid"),
        requires_key=requires_key,
        key_help_url=raw.get("key_help_url"),
        note=_PROVIDER_NOTES.get(provider_id),
    )


@router.get("", response_model=ProvidersResponse)
async def list_providers(user_id: CurrentUser) -> ProvidersResponse:
    """Derive provider options per task from services.yaml (stubs hidden)."""
    registry = get_registry()
    grouped: dict[str, list[ProviderOption]] = {"script": [], "image": [], "voice": []}
    for provider_id, raw in registry.services_raw.items():
        if provider_id.startswith("stub-"):
            continue
        tasks = (raw.get("tasks") or {}).keys()
        opt = _provider_option(provider_id, raw)
        for task_value in tasks:
            try:
                field = _TASK_TO_FIELD[Task(task_value)]
            except (ValueError, KeyError):
                continue
            grouped[field].append(opt)
    return ProvidersResponse(
        script=grouped["script"], image=grouped["image"], voice=grouped["voice"]
    )
