"""Request/response models for the API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class SystemStatus(BaseModel):
    first_boot_required: bool
    version: str


# Cap password length so an oversized input can't turn Argon2 hashing into a DoS.
_MAX_PASSWORD = 1024


class FirstBootRequest(BaseModel):
    username: str = Field(min_length=3, max_length=150)
    # Minimum length is enforced against settings in the handler.
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD)


class LoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=150)
    password: str = Field(min_length=1, max_length=_MAX_PASSWORD)


class UserOut(BaseModel):
    id: int
    username: str
    role: str


class MessageOut(BaseModel):
    detail: str
