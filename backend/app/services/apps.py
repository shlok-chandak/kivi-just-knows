"""Canonical app names, so episode grouping treats one app as one app:
"1Password", "1password" and "1 Password" are the same.
"""


_SEPARATORS = str.maketrans("", "", " -_.")


def normalise_app(app: str | None) -> str:
    return (app or "").strip().lower().translate(_SEPARATORS)
