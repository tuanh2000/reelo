"""HuggingFace voice/inference client — scaffold (Module 3).

Placeholder so ``services.yaml`` can list ``hf`` in ``routing.fallback`` without
breaking resolution. :meth:`is_available` returns ``False`` so the BYOK-aware
fallback chain skips it until implemented. Implementing it later = fill in the
capability method(s) + flip ``is_available``; no registry change needed.
"""

from __future__ import annotations

from clients.base import AIClient, CallContext, Task


class HuggingFaceClient(AIClient):
    """Not implemented yet — fallback skips it (``is_available`` is False)."""

    capabilities = {Task.GENERATE_VOICE}
    cost_tier = "paid"
    requires_key = True

    async def is_available(self, ctx: CallContext) -> bool:
        return False

    async def validate_key(self, ctx: CallContext) -> bool:
        return False


__all__ = ["HuggingFaceClient"]
