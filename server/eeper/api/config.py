"""Runtime configuration, loaded from ``EEPER_*`` environment variables.

No secrets have defaults: ``EEPER_DATABASE_URL`` and ``EEPER_SECRET_KEY`` are
required, so the service refuses to start without the values `install.sh`
generates. This is the code-level half of the "no default credentials" stance.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EEPER_", extra="ignore")

    # Required — no defaults on purpose (see module docstring).
    database_url: str = Field(min_length=1)
    secret_key: str = Field(min_length=16)

    # Session cookie behaviour.
    session_cookie_name: str = "eeper_session"
    session_max_age_seconds: int = 60 * 60 * 12  # 12 hours

    # Minimum admin password length enforced by the first-boot wizard.
    min_password_length: int = 12


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]  # values come from the environment
