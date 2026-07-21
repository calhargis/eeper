"""Authentication endpoints: login (+lockout, +TOTP), refresh, logout, TOTP setup."""

from __future__ import annotations

from datetime import datetime, timedelta

import pyotp
from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.auth_service import logout_session, rotate_refresh, start_session
from eeper.api.config import Settings
from eeper.api.cookies import clear_auth_cookies, read_persist_marker
from eeper.api.dependencies import CurrentUser, NowDep, SessionDep, SettingsDep
from eeper.api.models import User
from eeper.api.schemas import (
    LoginRequest,
    LoginResult,
    MessageOut,
    TotpActivateRequest,
    TotpEnrollResponse,
    TotpVerifyRequest,
    UserOut,
)
from eeper.api.security import verify_dummy, verify_password
from eeper.api.tokens import create_totp_challenge, decode_totp_challenge

router = APIRouter(prefix="/auth", tags=["auth"])

# Generic auth-failure response. Using one status/message for wrong password,
# unknown user, AND locked account avoids leaking which usernames exist.
_INVALID = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid username or password")


def _user_out(user: User) -> UserOut:
    return UserOut(id=user.id, username=user.username, role=user.role)


def _clear_lockout_if_elapsed(user: User, now: datetime) -> bool:
    """Reset lockout state once the window has passed. Returns True if the user
    is still locked."""
    if user.locked_until is None:
        return False
    if user.locked_until <= now:
        user.failed_login_count = 0
        user.locked_until = None
        return False
    return True


async def _register_failure(
    user: User, session: AsyncSession, settings: Settings, now: datetime
) -> None:
    """Count a failed credential/second-factor attempt and lock after N."""
    user.failed_login_count += 1
    if user.failed_login_count >= settings.max_failed_logins:
        user.locked_until = now + timedelta(seconds=settings.lockout_seconds)
    await session.commit()


@router.post("/login", response_model=LoginResult)
async def login(
    body: LoginRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    now: NowDep,
) -> LoginResult:
    user = (
        await session.execute(select(User).where(User.username == body.username))
    ).scalar_one_or_none()
    if user is None:
        verify_dummy(body.password)  # equalize timing for unknown users
        raise _INVALID

    # Locked accounts fail exactly like a wrong password (no 429 oracle).
    if _clear_lockout_if_elapsed(user, now):
        verify_dummy(body.password)
        raise _INVALID

    if not verify_password(user.password_hash, body.password):
        await _register_failure(user, session, settings, now)
        raise _INVALID

    # Correct password. If TOTP is on, hand out a challenge WITHOUT clearing the
    # lockout counter — the second factor is still pending, so failed TOTP codes
    # keep accumulating toward a lock (they can't be reset by re-login).
    if user.totp_enabled:
        await session.commit()
        challenge = create_totp_challenge(
            settings.secret_key,
            user_id=user.id,
            now=now,
            ttl_seconds=settings.totp_challenge_ttl_seconds,
        )
        return LoginResult(totp_required=True, challenge=challenge)

    user.failed_login_count = 0
    user.locked_until = None
    await start_session(session, response, settings, user, now, persist=body.remember)
    return LoginResult(user=_user_out(user))


@router.post("/totp/verify", response_model=LoginResult)
async def totp_verify(
    body: TotpVerifyRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    now: NowDep,
) -> LoginResult:
    user_id = decode_totp_challenge(settings.secret_key, body.challenge)
    if user_id is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired challenge")
    user = await session.get(User, user_id)
    if user is None or not user.totp_enabled or not user.totp_secret:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid challenge")

    # The second factor is rate-limited by the same lockout as the password.
    if _clear_lockout_if_elapsed(user, now):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many attempts; try again later")
    if not pyotp.TOTP(user.totp_secret).verify(body.code, valid_window=1):
        await _register_failure(user, session, settings, now)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code")

    user.failed_login_count = 0
    user.locked_until = None
    await start_session(session, response, settings, user, now, persist=body.remember)
    return LoginResult(user=_user_out(user))


@router.post("/refresh", response_model=UserOut)
async def refresh(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    now: NowDep,
) -> UserOut:
    token = request.cookies.get(settings.refresh_cookie_name)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "No refresh token")
    persist = read_persist_marker(request, settings)
    user = await rotate_refresh(session, response, settings, token, now, persist=persist)
    if user is None:
        clear_auth_cookies(response, settings)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or expired refresh token")
    return _user_out(user)


@router.post("/logout", response_model=MessageOut)
async def logout(
    request: Request,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
) -> MessageOut:
    token = request.cookies.get(settings.refresh_cookie_name)
    await logout_session(session, token)
    clear_auth_cookies(response, settings)
    return MessageOut(detail="Logged out")


@router.get("/session", response_model=UserOut)
async def current_session(user: CurrentUser) -> UserOut:
    return _user_out(user)


@router.post("/totp/enroll", response_model=TotpEnrollResponse)
async def totp_enroll(
    user: CurrentUser, session: SessionDep, settings: SettingsDep
) -> TotpEnrollResponse:
    secret: str = pyotp.random_base32()
    user.totp_secret = secret
    user.totp_enabled = False  # not active until a valid code is confirmed
    await session.commit()
    uri: str = pyotp.TOTP(secret).provisioning_uri(
        name=user.username, issuer_name=settings.totp_issuer
    )
    return TotpEnrollResponse(secret=secret, provisioning_uri=uri)


@router.post("/totp/activate", response_model=MessageOut)
async def totp_activate(
    body: TotpActivateRequest, user: CurrentUser, session: SessionDep
) -> MessageOut:
    if not user.totp_secret:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No TOTP enrollment in progress")
    if not pyotp.TOTP(user.totp_secret).verify(body.code, valid_window=1):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid code")
    user.totp_enabled = True
    await session.commit()
    return MessageOut(detail="TOTP enabled")
