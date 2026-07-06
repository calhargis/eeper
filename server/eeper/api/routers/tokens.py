"""Scoped API tokens for integrations (Bearer auth). Minting is admin-only.

A token only reaches admin-only endpoints if it carries the ``admin`` scope
(see dependencies.require_admin); by default a token is least-privilege.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from eeper.api.dependencies import AdminUser, SessionDep
from eeper.api.models import ApiToken
from eeper.api.schemas import ApiTokenCreated, ApiTokenOut, CreateApiTokenRequest, MessageOut
from eeper.api.security import hash_token
from eeper.api.tokens import generate_opaque_token

router = APIRouter(prefix="/tokens", tags=["tokens"])


def _scopes_to_str(scopes: list[str]) -> str:
    return ",".join(sorted({s.strip() for s in scopes if s.strip()}))


def _scopes_to_list(scopes: str) -> list[str]:
    return [s for s in scopes.split(",") if s]


def _token_out(token: ApiToken) -> ApiTokenOut:
    return ApiTokenOut(
        id=token.id,
        name=token.name,
        scopes=_scopes_to_list(token.scopes),
        created_at=token.created_at,
        last_used_at=token.last_used_at,
        revoked=token.revoked,
    )


@router.get("", response_model=list[ApiTokenOut])
async def list_tokens(admin: AdminUser, session: SessionDep) -> list[ApiTokenOut]:
    tokens = (
        (
            await session.execute(
                select(ApiToken).where(ApiToken.user_id == admin.id).order_by(ApiToken.id)
            )
        )
        .scalars()
        .all()
    )
    return [_token_out(t) for t in tokens]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ApiTokenCreated)
async def create_token(
    body: CreateApiTokenRequest, admin: AdminUser, session: SessionDep
) -> ApiTokenCreated:
    secret = generate_opaque_token()
    token = ApiToken(
        user_id=admin.id,
        name=body.name,
        token_hash=hash_token(secret),
        scopes=_scopes_to_str(body.scopes),
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)
    # The plaintext token is returned once here and never stored in the clear.
    return ApiTokenCreated(**_token_out(token).model_dump(), token=secret)


@router.delete("/{token_id}", response_model=MessageOut)
async def revoke_token(token_id: int, admin: AdminUser, session: SessionDep) -> MessageOut:
    token = await session.get(ApiToken, token_id)
    if token is None or token.user_id != admin.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Token not found")
    token.revoked = True
    await session.commit()
    return MessageOut(detail="Token revoked")
