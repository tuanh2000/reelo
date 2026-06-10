"""Usage dashboard (Module 3: reelo-ai-services).

``GET /usage`` returns the per-user usage rows plus a total cost (M3-6).
"""

from __future__ import annotations

from fastapi import APIRouter

from db.repository import UsageRepo
from web.deps import CurrentUser, DbSession
from web.schemas import UsageResponse, UsageRow

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("", response_model=UsageResponse)
async def get_usage(user_id: CurrentUser, db: DbSession) -> UsageResponse:
    """Per-user usage + total cost."""
    repo = UsageRepo(db)
    rows = await repo.list_for_user(user_id)
    usage = [
        UsageRow(
            provider=r.provider,
            task=r.task,
            units=r.units,
            cost=r.cost,
            ts=r.ts.isoformat() if r.ts else "",
        )
        for r in rows
    ]
    total = sum((r.cost or 0.0) for r in rows)
    total_cost = total if any(r.cost is not None for r in rows) else None
    return UsageResponse(usage=usage, total_cost=total_cost)
