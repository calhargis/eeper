"""eeper server-side services.

This package is intentionally minimal at Phase 0; concrete services (api,
insight-engine, recorder) land in later phases as subpackages. It exists now so
the lint and type-check pipeline runs against real, typed code.
"""

__all__ = ["__version__"]

__version__: str = "0.0.0"
