"""Auth cookie helpers — one place for the security attributes.

Cookies are httpOnly + Secure + SameSite=Lax. SameSite=Lax means the browser
withholds them on cross-site POST/PUT/DELETE, which provides CSRF protection for
state-changing requests. The refresh cookie is additionally path-scoped so it is
only sent to the auth endpoints.
"""

from __future__ import annotations

from fastapi import Request, Response

from eeper.api.config import Settings

# "Remember me": a persistent cookie carries a Max-Age so it survives a browser restart;
# a non-persistent ("session") cookie omits it and is dropped when the browser closes.


def set_access_cookie(
    response: Response, settings: Settings, token: str, persist: bool = True
) -> None:
    response.set_cookie(
        key=settings.access_cookie_name,
        value=token,
        max_age=settings.access_ttl_seconds if persist else None,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


def set_refresh_cookie(
    response: Response, settings: Settings, token: str, persist: bool = True
) -> None:
    response.set_cookie(
        key=settings.refresh_cookie_name,
        value=token,
        max_age=settings.refresh_ttl_seconds if persist else None,
        httponly=True,
        secure=True,
        samesite="lax",
        path=settings.refresh_cookie_path,
    )


def set_persist_marker(response: Response, settings: Settings, persist: bool) -> None:
    """Remember the "remember me" choice so a token refresh preserves it — without a DB
    column. Non-sensitive ("1"/"0"); a session cookie when not persisting, so it too is
    dropped on browser close. Scoped to the auth path like the refresh cookie."""
    response.set_cookie(
        key=settings.persist_cookie_name,
        value="1" if persist else "0",
        max_age=settings.refresh_ttl_seconds if persist else None,
        httponly=True,
        secure=True,
        samesite="lax",
        path=settings.refresh_cookie_path,
    )


def read_persist_marker(request: Request, settings: Settings) -> bool:
    # Default True so sessions created before this feature keep their persistent behavior.
    return request.cookies.get(settings.persist_cookie_name, "1") != "0"


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
    response.delete_cookie(
        key=settings.persist_cookie_name,
        path=settings.refresh_cookie_path,
        httponly=True,
        secure=True,
        samesite="lax",
    )
