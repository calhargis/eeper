"""Account endpoints. ``/me`` is the protected endpoint the M0.2 criteria assert
returns 401 before the wizard completes and after logout."""

from __future__ import annotations

from fastapi import APIRouter

from eeper.api.dependencies import CurrentUser
from eeper.api.schemas import UserOut

router = APIRouter(tags=["account"])


@router.get("/me", response_model=UserOut)
async def read_me(user: CurrentUser) -> UserOut:
    return UserOut(id=user.id, username=user.username, role=user.role)
