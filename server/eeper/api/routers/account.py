"""Account endpoints. ``/me`` is the protected endpoint the M0.2 criteria assert
returns 401 before the wizard completes and after logout; ``/me/password`` lets a
signed-in user change their own password."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response, status

from eeper.api.auth_service import (
    clear_lockout_if_elapsed,
    register_failed_attempt,
    revoke_all_sessions,
    start_session,
)
from eeper.api.cookies import read_persist_marker
from eeper.api.dependencies import CurrentUser, NowDep, SessionDep, SettingsDep
from eeper.api.schemas import ChangePasswordRequest, MessageOut, UserOut
from eeper.api.security import hash_password, verify_password

router = APIRouter(tags=["account"])


@router.get("/me", response_model=UserOut)
async def read_me(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, username=user.username, role=user.role)


@router.post("/me/password", response_model=MessageOut)
async def change_password(
    body: ChangePasswordRequest,
    request: Request,
    response: Response,
    user: CurrentUser,
    session: SessionDep,
    settings: SettingsDep,
    now: NowDep,
) -> MessageOut:
    """Change the signed-in user's password.

    Re-authenticates with the CURRENT password (so a merely-open session — e.g. a
    borrowed/hijacked one — can't silently change it), guarded by the SAME account lockout
    as login so it can't be brute-forced, and enforces the length policy. On success it
    revokes every refresh session — signing OTHER browser logins out (a still-valid access
    token elsewhere lapses within its short 15-min TTL; API tokens are separate credentials
    managed on the tokens page) — and issues a fresh session for THIS device, so the caller
    stays signed in with new cookies.
    """
    # Re-auth, rate-limited by the shared lockout (Argon2 is already slow; this bounds a
    # hijacked-session brute-force). A locked account is masked as a wrong password.
    if clear_lockout_if_elapsed(user, now):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Current password is incorrect.")
    if not verify_password(user.password_hash, body.current_password):
        await register_failed_attempt(user, session, settings, now)
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Current password is incorrect.")
    if len(body.new_password) < settings.min_password_length:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"New password must be at least {settings.min_password_length} characters.",
        )
    if body.new_password == body.current_password:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "New password must be different from the current one.",
        )

    # Verified. Clear the lockout counter, set the new hash, revoke every session, then
    # issue a fresh one for this device — start_session commits it all together, keeping
    # the caller's "remember me" choice (the persist marker is now "/"-scoped, so this
    # endpoint actually receives it).
    user.failed_login_count = 0
    user.locked_until = None
    user.password_hash = hash_password(body.new_password)
    await revoke_all_sessions(session, user.id)
    persist = read_persist_marker(request, settings)
    await start_session(session, response, settings, user, now, persist=persist)
    return MessageOut(detail="Password changed")
