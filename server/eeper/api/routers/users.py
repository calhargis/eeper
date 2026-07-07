"""Admin-only user management. Viewer accounts are denied here (403)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from eeper.api.dependencies import AdminUser, SessionDep
from eeper.api.models import User
from eeper.api.schemas import CreateUserRequest, UserOut
from eeper.api.security import hash_password

router = APIRouter(prefix="/users", tags=["users"])


@router.get("", response_model=list[UserOut])
async def list_users(_admin: AdminUser, session: SessionDep) -> list[UserOut]:
    users = (await session.execute(select(User).order_by(User.id))).scalars().all()
    return [UserOut(id=u.id, username=u.username, role=u.role) for u in users]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=UserOut)
async def create_user(body: CreateUserRequest, _admin: AdminUser, session: SessionDep) -> UserOut:
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        role=body.role,
    )
    session.add(user)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Username already exists") from exc
    await session.refresh(user)
    return UserOut(id=user.id, username=user.username, role=user.role)
