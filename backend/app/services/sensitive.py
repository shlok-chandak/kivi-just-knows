"""Content categories that must never become a memory.

Distinct from the app denylist: that rule blocks a *source*, this one blocks a
*topic*. An event on a sensitive topic is still stored, so the user can find
their own dictation, but nothing is extracted from it.
"""

SENSITIVE_CATEGORIES: tuple[str, ...] = (
    "health",
    "religion",
    "politics",
    "race_ethnicity",
    "sexual_orientation",
    "financial_account",
    "credentials",
)

REJECTION_RULE = "sensitive_category"
