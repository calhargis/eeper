"""eeper labeled audio fixture library tooling (M2.0).

Produces a frozen, versioned library (``fixtures-v1``) of synthesized nursery
scenes for the M2.3 cry-detection quality gate — from a per-clip manifest alone,
with NO third-party audio committed to the repo. Source clips are fetched at build
time and checksum-verified; scenes are synthesized deterministically with Scaper.
"""

FIXTURES_VERSION = "fixtures-v1"
