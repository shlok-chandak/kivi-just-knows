"""Shared test setup.

Tests must not call the model provider. A test that does is slow, costs
quota, fails when the network does, and asserts against output that changes
between runs. The guard below makes that a loud error rather than a silent
bill, so reaching for a stub is the easy path.
"""

import pytest

from app.services import consolidate as consolidate_module

# Every module that reaches for a client. A new one must be added here, or
# its tests will quietly start calling the real provider.
_LLM_CONSUMERS = (consolidate_module,)


class RealProviderCalledInTests(RuntimeError):
    pass


def _refuse() -> None:
    raise RealProviderCalledInTests(
        "a test tried to call the real model provider. Stub it instead: "
        "monkeypatch.setattr(<module>, 'get_client', lambda: StubClient(...))"
    )


@pytest.fixture(autouse=True)
def no_llm(monkeypatch):
    """Block real provider calls in every test.

    Autouse, so it applies without being asked for. A test that wants a
    stubbed model patches the same name afterwards, which takes precedence.
    """
    for module in _LLM_CONSUMERS:
        monkeypatch.setattr(module, "get_client", _refuse)
