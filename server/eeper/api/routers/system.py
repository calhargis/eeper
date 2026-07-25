"""System endpoints: status and the first-boot wizard."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from eeper import __version__
from eeper.api.auth_service import start_session
from eeper.api.dependencies import NowDep, SessionDep, SettingsDep, admin_exists
from eeper.api.models import User
from eeper.api.schemas import FirstBootRequest, SystemStatus, UserOut
from eeper.api.security import hash_password

router = APIRouter(prefix="/system", tags=["system"])

# Transaction-scoped advisory lock key that serializes first-boot attempts.
_FIRST_BOOT_LOCK = 8675309


@router.get("/status", response_model=SystemStatus)
async def get_status(session: SessionDep, settings: SettingsDep) -> SystemStatus:
    return SystemStatus(
        first_boot_required=not await admin_exists(session),
        version=__version__,
        lite=settings.lite,
    )


@router.post("/first-boot", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def first_boot(
    body: FirstBootRequest,
    response: Response,
    session: SessionDep,
    settings: SettingsDep,
    now: NowDep,
) -> UserOut:
    """Create the first admin (and log them in). Refuses once any user exists."""
    # Serialize concurrent first-boot attempts (held until this transaction ends)
    # so the check-then-insert below can't race two admins into existence.
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _FIRST_BOOT_LOCK})
    if await admin_exists(session):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already initialized")
    if len(body.password) < settings.min_password_length:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Password must be at least {settings.min_password_length} characters",
        )

    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role="admin",
    )
    session.add(user)
    try:
        await session.commit()  # persist the admin + release the advisory lock
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Already initialized") from exc
    await session.refresh(user)

    await start_session(session, response, settings, user, now)
    return UserOut(id=user.id, username=user.username, role=user.role)
