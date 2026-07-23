"""Session lifecycle: issuing, rotating, and revoking tokens.

Access is a short-lived JWT (stateless). Refresh is an opaque token stored
hashed and grouped into a per-login *family*. Rotation issues a new refresh and
marks the old one rotated; presenting an already-rotated token is treated as
theft and revokes the whole family.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from fastapi import Response
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from eeper.api.config import Settings
from eeper.api.cookies import set_access_cookie, set_persist_marker, set_refresh_cookie
from eeper.api.models import ApiToken, RefreshToken, User
from eeper.api.security import hash_token
from eeper.api.tokens import create_access_token, generate_opaque_token


def _issue_access_cookie(
    response: Response, settings: Settings, user: User, now: datetime, persist: bool = True
) -> None:
    access = create_access_token(
        settings.secret_key,
        user_id=user.id,
        role=user.role,
        now=now,
        ttl_seconds=settings.access_ttl_seconds,
    )
    set_access_cookie(response, settings, access, persist=persist)


async def _new_refresh(
    session: AsyncSession, settings: Settings, user_id: int, family_id: str, now: datetime
) -> str:
    token = generate_opaque_token()
    session.add(
        RefreshToken(
            user_id=user_id,
            family_id=family_id,
            token_hash=hash_token(token),
            expires_at=now + timedelta(seconds=settings.refresh_ttl_seconds),
        )
    )
    return token


async def revoke_family(session: AsyncSession, family_id: str) -> None:
    await session.execute(
        update(RefreshToken).where(RefreshToken.family_id == family_id).values(revoked=True)
    )


async def revoke_all_sessions(session: AsyncSession, user_id: int) -> None:
    """Revoke every refresh family for a user. Used on a password change so any other
    signed-in device is logged out; the caller then issues a fresh session for itself.
    Does not commit — the caller commits (start_session does)."""
    await session.execute(
        update(RefreshToken).where(RefreshToken.user_id == user_id).values(revoked=True)
    )


def clear_lockout_if_elapsed(user: User, now: datetime) -> bool:
    """Reset a user's brute-force lockout once its window has passed. Returns True if the
    account is still locked. Shared by the login and change-password re-auth paths so a
    single account lockout governs every credential check."""
    if user.locked_until is None:
        return False
    if user.locked_until <= now:
        user.failed_login_count = 0
        user.locked_until = None
        return False
    return True


async def register_failed_attempt(
    user: User, session: AsyncSession, settings: Settings, now: datetime
) -> None:
    """Count a failed credential attempt and lock the account after N. Commits."""
    user.failed_login_count += 1
    if user.failed_login_count >= settings.max_failed_logins:
        user.locked_until = now + timedelta(seconds=settings.lockout_seconds)
    await session.commit()


async def start_session(
    session: AsyncSession,
    response: Response,
    settings: Settings,
    user: User,
    now: datetime,
    persist: bool = True,
) -> None:
    """Begin a fresh login session (new family) and set the cookies. ``persist`` is the
    "remember me" choice: persistent cookies (survive a browser restart) vs. session cookies."""
    family_id = uuid.uuid4().hex
    refresh = await _new_refresh(session, settings, user.id, family_id, now)
    await session.commit()
    set_refresh_cookie(response, settings, refresh, persist=persist)
    _issue_access_cookie(response, settings, user, now, persist=persist)
    set_persist_marker(response, settings, persist)


async def rotate_refresh(
    session: AsyncSession,
    response: Response,
    settings: Settings,
    presented_token: str,
    now: datetime,
    persist: bool = True,
) -> User | None:
    """Validate + rotate a refresh token. Returns the user, or None if invalid.

    The row is locked FOR UPDATE so concurrent rotations of the same token are
    serialized: exactly one wins, and any later presentation sees rotated=True
    and is treated as reuse — closing the check-then-act race.
    """
    token_hash = hash_token(presented_token)
    rt = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == token_hash).with_for_update()
        )
    ).scalar_one_or_none()

    if rt is None or rt.revoked or rt.expires_at <= now:
        return None
    if rt.rotated:
        # An already-rotated token was replayed → the family is compromised.
        await revoke_family(session, rt.family_id)
        await session.commit()
        return None

    rt.rotated = True
    new_refresh = await _new_refresh(session, settings, rt.user_id, rt.family_id, now)
    user = await session.get(User, rt.user_id)
    if user is None:
        return None
    await session.commit()

    set_refresh_cookie(response, settings, new_refresh, persist=persist)
    _issue_access_cookie(response, settings, user, now, persist=persist)
    set_persist_marker(response, settings, persist)
    return user


async def logout_session(session: AsyncSession, presented_token: str | None) -> None:
    """Revoke the presented refresh token's whole family."""
    if not presented_token:
        return
    rt = (
        await session.execute(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(presented_token))
        )
    ).scalar_one_or_none()
    if rt is not None:
        await revoke_family(session, rt.family_id)
        await session.commit()


async def resolve_api_token(
    session: AsyncSession, presented_token: str, now: datetime
) -> ApiToken | None:
    """Resolve a Bearer API token record (and stamp last_used_at). Returns the
    ApiToken so the caller can enforce its scopes."""
    at = (
        await session.execute(
            select(ApiToken).where(ApiToken.token_hash == hash_token(presented_token))
        )
    ).scalar_one_or_none()
    if at is None or at.revoked:
        return None
    at.last_used_at = now
    await session.commit()
    return at
