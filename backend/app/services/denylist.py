"""Apps whose dictations are never stored.

Enforced in code rather than by a model: this is a hard guarantee, and a
classifier that is right 99% of the time still leaks one password in a hundred.
"""

DENYLISTED_APPS: frozenset[str] = frozenset(
    {
        "password_manager",
        "banking",
        "1password",
        "keychain",
        "health",
    }
)


def normalise_app(app: str | None) -> str:
    return (app or "").strip().lower().replace(" ", "_").replace("-", "_")


def is_denylisted(app: str | None) -> bool:
    return normalise_app(app) in DENYLISTED_APPS


def denylist_reason(app: str | None) -> str:
    return f"denylisted_app:{normalise_app(app)}"
