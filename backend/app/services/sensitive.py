"""Content categories that must never become a memory.

The rule blocks a topic, not a source. An event on a sensitive topic is still
stored, so the user can find their own dictation, but nothing is extracted
from it and no memory is formed.
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
