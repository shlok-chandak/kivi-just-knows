"""Which apps are mined for beliefs, and which are only indexed.

A code editor is where a person instructs a tool, not where they record what
is true. The dictation is still stored and searchable; it simply produces no
claims. These tests pin both halves, because dropping the storage as well
would lose "what was I working on in Cursor on Tuesday".
"""

from app.services.apps import NO_EXTRACT_APPS, extractable, normalise_app


def test_editors_are_not_mined():
    assert extractable("cursor") is False
    assert extractable("VS Code") is False
    assert extractable("vscode") is False


def test_everywhere_a_person_talks_is_mined():
    for app in ("slack", "gmail", "whatsapp", "notion", "linear", "notes"):
        assert extractable(app) is True, app


def test_spelling_does_not_decide_the_rule():
    """The same editor written three ways is the same editor."""
    assert extractable("VS-Code") is False
    assert extractable("vs code") is False
    assert normalise_app("VS Code") in NO_EXTRACT_APPS


def test_an_unknown_app_is_mined():
    """Silence about an app means keep its content, not discard it."""
    assert extractable("obsidian") is True
    assert extractable(None) is True
