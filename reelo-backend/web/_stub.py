"""Shared 501 helper for unimplemented endpoints (Phase 1 skeleton)."""

from __future__ import annotations

from fastapi import HTTPException, status


def not_implemented(owner: str, what: str) -> HTTPException:
    """Return a 501 HTTPException attributing the endpoint to its owning module."""
    return HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"{what} — not implemented yet (owned by {owner}).",
    )


__all__ = ["not_implemented"]
