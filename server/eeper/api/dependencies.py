"""Shared FastAPI dependencies (auth resolution + guards)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import Depends, Request, status
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.auth_service import resolve_api_token
from eeper.api.clock import get_now
from eeper.api.config import Settings, get_settings
from eeper.api.db import get_session
from eeper.api.models import User
from eeper.api.tokens import decode_access_token

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]
NowDep = Annotated[datetime, Depends(get_now)]

# Scope an API token must carry to reach admin-only endpoints. Cookie-based admin
# sessions are unrestricted; API tokens are least-privilege unless scoped up.
ADMIN_SCOPE = "admin"

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
)


def _parse_scopes(scopes: str) -> list[str]:
    return [s for s in scopes.split(",") if s]


async def get_current_user(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
    now: NowDep,
) -> User:
    """Resolve the caller from the access cookie (browser) or a Bearer API token.

    Records how the request authenticated on ``request.state`` so guards can
    apply API-token scoping.
    """
    # 1) Access-token cookie — a full browser session.
    token = request.cookies.get(settings.access_cookie_name)
    if token:
        payload = decode_access_token(settings.secret_key, token)
        if payload is not None:
            try:
                user_id = int(payload["sub"])
            except (KeyError, TypeError, ValueError):
                user_id = None
            if user_id is not None:
                user = await session.get(User, user_id)
                if user is not None:
                    request.state.api_token_scopes = None  # cookie session: unrestricted
                    return user

    # 2) Bearer API token (integrations) — carries scopes.
    authorization = request.headers.get("authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() == "bearer" and credential:
        api_token = await resolve_api_token(session, credential.strip(), now)
        if api_token is not None:
            user = await session.get(User, api_token.user_id)
            if user is not None:
                request.state.api_token_scopes = _parse_scopes(api_token.scopes)
                return user

    raise _UNAUTHENTICATED


CurrentUser = Annotated[User, Depends(get_current_user)]


async def require_admin(request: Request, user: CurrentUser) -> User:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Admin privileges required")
    # An API token reaches admin endpoints only if explicitly scoped for it.
    scopes = getattr(request.state, "api_token_scopes", None)
    if scopes is not None and ADMIN_SCOPE not in scopes:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "This API token is not permitted for admin operations",
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]


async def admin_exists(session: AsyncSession) -> bool:
    result = await session.execute(select(User.id).limit(1))
    return result.first() is not None
