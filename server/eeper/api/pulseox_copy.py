"""Pulse-oximetry safety copy (M4.2).

This is the ONE place eeper is allowed to name the medical concepts it disclaims — its
whole purpose is to state the limitations plainly — so it is the single reviewed
exemption to the clinical-terms copy lint (see server/scripts/clinical_terms_lint.py).
Every other user-facing string is held to the denylist. Changing this text or lowering
its bar is a safety-boundary change (see CONTRIBUTING.md) and must be reviewed as one.

Stance (docs/MASTER_PLAN.md §2): pulse-oximetry is an OPTIONAL, INSIGHTS-ONLY input.
Heart-rate and its variability are features for sleep-state estimation and long-term
trend context — never a vital-sign readout, never an alarm, never a diagnosis.
"""

from __future__ import annotations

# Bump when the text materially changes: a prior acknowledgment of an older version no
# longer counts, so an admin must read and acknowledge the new text before pulse-ox works.
DISCLAIMER_VERSION = "1"

# The full disclaimer an admin must acknowledge before pulse-ox can be enabled.
DISCLAIMER_TEXT = (
    "eeper is a sleep-insight and awareness tool, not a medical device. It cannot "
    "detect, predict, or prevent any medical condition, including apnea or SIDS.\n\n"
    "Pulse-oximetry input is optional and insights-only. Heart-rate and its variability "
    "are used only as features for sleep-state estimation and are shown as long-term "
    "trend context. eeper does not present heart-rate or blood-oxygen as a live "
    "readout, and it never raises an alarm on them.\n\n"
    "Consumer optical sensors such as the MAX3010x are not medical-grade. Their readings "
    "are least reliable in exactly the ranges that would matter for health, which can "
    "cause both false alarm and false reassurance. Never rely on eeper for your child's "
    "safety, and always follow safe-sleep guidance.\n\n"
    "By enabling pulse-oximetry input you confirm that you have read and understood "
    "these limitations."
)

# A short reminder the UI shows on every pulse-ox view (M4.2 [AUTO]: asserted present).
ACCURACY_CAVEAT = "Insights-only trend context from a consumer sensor — not a vital-sign readout."

# A pointer to safe-sleep guidance shown alongside the disclaimer (onboarding requirement).
SAFE_SLEEP_URL = "https://safetosleep.nichd.nih.gov/"
