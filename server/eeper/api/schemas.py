"""Request/response models for the API."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit

from pydantic import AwareDatetime, BaseModel, Field, ValidationInfo, field_validator

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


# ── cameras ──────────────────────────────────────────────────────────────────


class CameraCreate(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    source_url: str = Field(min_length=1, max_length=500)

    @field_validator("source_url")
    @classmethod
    def _require_rtsp(cls, value: str) -> str:
        # The source is handed to ffprobe and go2rtc verbatim. Restrict it to
        # rtsp(s):// so a value can't select another ffprobe protocol (file:,
        # http:, data:, …) or be parsed as an option — closing off local-file
        # reads / SSRF and keeping the "RTSP contract" honest.
        scheme = urlsplit(value).scheme.lower()
        if scheme not in ("rtsp", "rtsps"):
            raise ValueError("source_url must be an rtsp:// or rtsps:// URL")
        return value


class CameraOut(BaseModel):
    id: int
    name: str
    # source_url is deliberately omitted: RTSP URLs embed camera credentials and
    # the internal camera IP. It is admin-only input and is never echoed back —
    # clients watch only via the api-relayed stream, never the raw source.
    codec: str
    width: int
    height: int
    enabled: bool
    has_audio: bool = False
    online: bool | None = None
    last_checked: datetime | None = None


# ── clips (recordings) ───────────────────────────────────────────────────────


class ClipCreate(BaseModel):
    # Timezone-aware only: naive input would later mix with the recorder's UTC-aware
    # segment times and raise. Clients send ISO-8601 with an offset (e.g. ...Z).
    start: AwareDatetime
    end: AwareDatetime

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, end: datetime, info: ValidationInfo) -> datetime:
        start = info.data.get("start")
        if start is not None and end <= start:
            raise ValueError("end must be after start")
        return end


class ClipOut(BaseModel):
    id: int
    camera_id: int
    requested_start: datetime
    requested_end: datetime
    actual_start: datetime
    actual_end: datetime
    duration_seconds: float
    size_bytes: int
    codec: str
    created_at: datetime
