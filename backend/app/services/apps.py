"""Canonical app names, so episode grouping treats one app as one app:
"1Password", "1password" and "1 Password" are the same.
"""


_SEPARATORS = str.maketrans("", "", " -_.")

# Apps where dictation is an instruction to a tool rather than a record of
# something true. "Refactor the retry handler" is a command being carried out
# as it is spoken, not a fact that outlives the moment.
NO_EXTRACT_APPS = frozenset({"cursor", "vscode", "vscodeinsiders", "zed"})


def normalise_app(app: str | None) -> str:
    return (app or "").strip().lower().translate(_SEPARATORS)


def extractable(app: str | None) -> bool:
    """Should beliefs be drawn from what was said here."""
    return normalise_app(app) not in NO_EXTRACT_APPS
