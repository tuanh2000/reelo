"""Google OAuth login + session (multi-tenant, integration §4).

Flow:
- ``GET /auth/login``    → redirect to Google's consent screen (Authlib).
- ``GET /auth/callback`` → exchange code, upsert the ``users`` row, set the
  session cookie (``user_id``), redirect to the UI.
- ``POST /auth/logout``  → clear the session.
- ``GET /auth/me``       → current user info (or 401).

The signed-cookie session middleware is installed in ``web.app``. OAuth login
is distinct from BYOK provider keys (Module 3) — this only identifies the user.
"""

from __future__ import annotations

import hashlib

from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from config import get_settings
from db.repository import UserRepo
from db.session import session_scope
from web.deps import SESSION_USER_KEY, get_current_user

router = APIRouter(prefix="/auth", tags=["auth"])

GOOGLE_DISCOVERY = "https://accounts.google.com/.well-known/openid-configuration"

_oauth: OAuth | None = None


def get_oauth() -> OAuth:
    """Lazily build the Authlib OAuth registry (so import stays env-free)."""
    global _oauth
    if _oauth is None:
        settings = get_settings()
        oauth = OAuth()
        oauth.register(
            name="google",
            client_id=settings.google_oauth_client_id,
            client_secret=settings.google_oauth_client_secret,
            server_metadata_url=GOOGLE_DISCOVERY,
            client_kwargs={"scope": "openid email profile"},
        )
        _oauth = oauth
    return _oauth


def _user_id_for(google_sub: str) -> str:
    """Stable internal user id derived from the Google subject id."""
    return "u_" + hashlib.sha256(google_sub.encode()).hexdigest()[:24]


@router.get("/login")
async def login(request: Request) -> RedirectResponse:
    settings = get_settings()
    if not settings.google_oauth_client_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Google OAuth is not configured (set GOOGLE_OAUTH_CLIENT_ID/SECRET).",
        )
    oauth = get_oauth()
    return await oauth.google.authorize_redirect(request, settings.google_oauth_redirect_uri)


@router.get("/callback")
async def callback(request: Request):
    settings = get_settings()
    oauth = get_oauth()
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    userinfo = token.get("userinfo") or {}
    google_sub = userinfo.get("sub")
    if not google_sub:
        raise HTTPException(status_code=400, detail="Google did not return a subject id")

    user_id = _user_id_for(google_sub)
    async with session_scope() as session:
        await UserRepo(session).upsert_from_oauth(
            user_id=user_id,
            google_sub=google_sub,
            email=userinfo.get("email", ""),
            name=userinfo.get("name"),
            picture=userinfo.get("picture"),
        )

    request.session[SESSION_USER_KEY] = user_id
    return RedirectResponse(url=settings.oauth_post_login_redirect)


@router.post("/logout")
async def logout(request: Request) -> JSONResponse:
    request.session.clear()
    return JSONResponse({"ok": True})


@router.get("/me")
async def me(request: Request) -> dict:
    user_id = await get_current_user(request)
    async with session_scope() as session:
        user = await UserRepo(session).get(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="Session user not found")
    return {"id": user.id, "email": user.email, "name": user.name, "picture": user.picture}


__all__ = ["router", "get_oauth"]
