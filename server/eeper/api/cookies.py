"""Auth cookie helpers — one place for the security attributes.

Cookies are httpOnly + Secure + SameSite=Lax. SameSite=Lax means the browser
withholds them on cross-site POST/PUT/DELETE, which provides CSRF protection for
state-changing requests. The refresh cookie is additionally path-scoped so it is
only sent to the auth endpoints.
"""

from __future__ import annotations

from fastapi import Response

from eeper.api.config import Settings


def set_access_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.access_cookie_name,
        value=token,
        max_age=settings.access_ttl_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def set_refresh_cookie(response: Response, settings: Settings, token: str) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_ttl_seconds,
        httponly=True,
        secure=True,
        samesite="lax",
        path=settings.refresh_cookie_path,
    )


def clear_auth_cookies(response: Response, settings: Settings) -> None:
    response.delete_cookie(
        key=settings.access_cookie_name,
        path="/",
        httponly=True,
        secure=True,
        samesite="lax",
    )
    response.delete_cookie(
        key=settings.refresh_cookie_name,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=True,
        samesite="lax",
    )
