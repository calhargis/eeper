"""Authentication endpoints: login, logout, and the current session."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import select

from eeper.api.cookies import clear_session_cookie, set_session_cookie
from eeper.api.dependencies import CurrentUser, SessionDep, SettingsDep
from eeper.api.models import User
from eeper.api.schemas import LoginRequest, MessageOut, UserOut
from eeper.api.security import issue_session, verify_dummy, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

_INVALID = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")


@router.post("/login", response_model=UserOut)
async def login(
    body: LoginRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> UserOut:
    user = (
        await session.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if user is None:
        verify_dummy(body.password)  # equalize timing for unknown users
        raise _INVALID
    if not verify_password(user.password_hash, body.password):
        raise _INVALID

    set_session_cookie(response, settings, issue_session(settings.secret_key, user.id))
    return UserOut(id=user.id, username=user.username, role=user.role)


@router.post("/logout", response_model=MessageOut)
async def logout(response: Response, settings: SettingsDep) -> MessageOut:
    clear_session_cookie(response, settings)
    return MessageOut(detail="Logged out")


@router.get("/session", response_model=UserOut)
async def current_session(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, username=user.username, role=user.role)
