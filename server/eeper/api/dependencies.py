"""Shared FastAPI dependencies (auth gate)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request, status
from fastapi.exceptions import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.config import Settings, get_settings
from eeper.api.db import get_session
from eeper.api.models import User
from eeper.api.security import read_session

SettingsDep = Annotated[Settings, Depends(get_settings)]
SessionDep = Annotated[AsyncSession, Depends(get_session)]

_UNAUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
)


async def get_current_user(
    request: Request,
    session: SessionDep,
    settings: SettingsDep,
) -> User:
    """Resolve the logged-in user from the session cookie, or raise 401."""
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise _UNAUTHENTICATED
    user_id = read_session(settings.secret_key, token, settings.session_max_age_seconds)
    if user_id is None:
        raise _UNAUTHENTICATED
    user = await session.get(User, user_id)
    if user is None:
        raise _UNAUTHENTICATED
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


async def admin_exists(session: AsyncSession) -> bool:
    result = await session.execute(select(User.id).limit(1))
    return result.first() is not None
