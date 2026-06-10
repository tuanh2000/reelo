"""FastAPI dependencies shared across routers.

- :func:`get_db` — async DB session.
- :func:`get_current_user` — resolves the session cookie to a ``user_id``;
  raises 401 if not authenticated. **Every protected route depends on this.**
- :data:`CurrentUser` / :data:`DbSession` — annotated aliases for terse handlers.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from db.session import get_session

SESSION_USER_KEY = "user_id"


async def get_db() -> AsyncSession:  # pragma: no cover - thin re-export
    async for s in get_session():
        yield s


async def get_current_user(request: Request) -> str:
    """Return the authenticated ``user_id`` from the signed session cookie.

    Raises:
        HTTPException(401): when there is no logged-in user.
    """
    user_id = request.session.get(SESSION_USER_KEY) if hasattr(request, "session") else None
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated. Sign in with Google at /auth/login.",
        )
    return user_id


CurrentUser = Annotated[str, Depends(get_current_user)]
DbSession = Annotated[AsyncSession, Depends(get_db)]


__all__ = ["get_db", "get_current_user", "CurrentUser", "DbSession", "SESSION_USER_KEY"]
