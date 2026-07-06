"""Request/response models for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Role = Literal["admin", "viewer"]

# Cap password length so an oversized input can't turn Argon2 hashing into a DoS.
_MAX_PASSWORD = 1024


class SystemStatus(BaseModel):
    first_boot_required: bool
    version: str


class UserOut(BaseModel):
    id: int
    username: str
    role: str


class MessageOut(BaseModel):
    detail: str


# ── auth ────────────────────────────────────────────────────────────────────


class FirstBootRequest(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    # Minimum length is enforced against settings in the handler.
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD)


class LoginResult(BaseModel):
    """Either an authenticated user (tokens set via cookies) or a TOTP challenge."""

    totp_required: bool = False
    challenge: str | None = None
    user: UserOut | None = None


class TotpVerifyRequest(BaseModel):
    challenge: str
    code: str = Field(min_length=6, max_length=8)


class TotpEnrollResponse(BaseModel):
    secret: str
    provisioning_uri: str


class TotpActivateRequest(BaseModel):
    code: str = Field(min_length=6, max_length=8)


# ── admin: users ─────────────────────────────────────────────────────────────


class CreateUserRequest(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    password: str = Field(min_length=12, max_length=_MAX_PASSWORD)
    role: Role = "viewer"


# ── admin: api tokens ────────────────────────────────────────────────────────


class CreateApiTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=list)


class ApiTokenOut(BaseModel):
    id: int
    name: str
    scopes: list[str]
    created_at: datetime
    last_used_at: datetime | None
    revoked: bool


class ApiTokenCreated(ApiTokenOut):
    # The plaintext token — returned once, at creation, and never again.
    token: str
