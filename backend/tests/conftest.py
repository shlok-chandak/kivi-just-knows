"""Shared test setup.

Two things tests are not allowed to reach: the model provider, and the
development database.

The provider, because a test that calls it is slow, costs quota, fails when
the network does, and asserts against output that changes between runs. The
guard below makes that a loud error rather than a silent bill.

The database, because tests are destructive by design and were pointed at
the same one everything else uses. They clean up by deleting the rows they
wrote, and one end-to-end test imports a fixture with --truncate, which
clears the events table outright. So running the suite deleted the imported
corpus -- which is exactly what happened.

Redirecting the connection is the fix rather than making each test tidier,
because it removes the whole class of problem: nothing a test does, however
careless, can reach data somebody was working with. The assertion afterwards
is the part that matters. Without it this file silently stops working the day
the variable is set some other way, and the accident it prevents is one
nobody notices until data is missing.

One-time setup, from the repository root:

    docker compose exec db psql -U kivi -d postgres \\
        -c "CREATE DATABASE kivi_test OWNER kivi;"
    docker compose run --rm \\
        -e DATABASE_URL=postgresql+psycopg://kivi:kivi@db:5432/kivi_test \\
        worker alembic upgrade head

Re-run the second command after adding a migration.
"""

import os
import uuid

_TEST_DATABASE = "kivi_test"
_DEFAULT_URL = "postgresql+psycopg://kivi:kivi@db:5432/kivi"


def _test_database_url() -> str:
    """The configured connection, with the database name swapped."""
    url = os.environ.get("DATABASE_URL") or _DEFAULT_URL
    base, _, _name = url.rpartition("/")
    return f"{base}/{_TEST_DATABASE}"


# Set before app.config is imported below. Settings reads the environment
# once, at import, and every module shares that one instance -- so this has
# to happen first or it does not happen at all.
os.environ["DATABASE_URL"] = _test_database_url()

import pytest  # noqa: E402

from app.config import settings  # noqa: E402
from app.services import composer as composer_module  # noqa: E402
from app.services import consolidate as consolidate_module  # noqa: E402
from app.services import parse as parse_module  # noqa: E402
from app.services import recall as recall_module  # noqa: E402
from app.services import restyle as restyle_module  # noqa: E402

if not settings.database_url.endswith(_TEST_DATABASE):
    raise RuntimeError(
        f"tests must run against {_TEST_DATABASE}, not "
        f"{settings.database_url.rpartition('/')[2]}. The redirect above did "
        f"not take effect, and running on would delete real data."
    )

# Distinct from the default user too, so an unscoped delete inside the test
# database still cannot match rows an import created there. Fixed rather than
# random, so leftovers from a killed run stay identifiable.
TEST_USER = uuid.UUID("00000000-0000-0000-0000-0000000000ff")

# Every module that reaches for a client. A new one must be added here, or
# its tests will quietly start calling the real provider.
_LLM_CONSUMERS = (
    composer_module,
    consolidate_module,
    parse_module,
    recall_module,
    restyle_module,
)


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
