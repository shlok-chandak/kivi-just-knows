"""Running the system with something withheld.

The property that matters is that withholding is temporary and local. An
exclusion that leaked into the next question, or that deleted anything,
would make every measurement taken with it worthless.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.config import settings
from app.db.session import SessionLocal
from app.models.memory import Memory
from app.services import retrieval
from app.services.ablation import NONE, Ablation

USER = settings.default_user_id
NOW = datetime(2026, 6, 17, 9, 0, tzinfo=timezone.utc)

# Distinctive enough that the trigram arm finds it without an embedding row.
NEEDLE = "The quarterly zephyr reconciliation runs on Tuesdays."


@pytest.fixture
def memory():
    session = SessionLocal()
    row = Memory(
        user_id=USER,
        type="fact",
        content=NEEDLE,
        claim_key=f"test:{uuid.uuid4()}",
        status="active",
        alpha=3.0,
        beta=1.0,
        observation_count=2,
        last_reinforced_at=NOW - timedelta(days=2),
    )
    session.add(row)
    session.commit()
    yield session, row
    session.execute(delete(Memory).where(Memory.id == row.id))
    session.commit()
    session.close()


def _find(session, cuts=NONE):
    hits = retrieval.search_memories(
        session, USER, "quarterly zephyr reconciliation", now=NOW, cuts=cuts
    )
    return [hit for hit in hits if hit.id]


def test_default_withholds_nothing():
    assert NONE.active is False
    assert NONE.allows("recall") is True


def test_excluded_memory_is_not_retrieved(memory):
    session, row = memory

    before = _find(session)
    during = _find(session, Ablation(exclude_memory_ids=frozenset({row.id})))

    assert row.id in {hit.id for hit in before}, "fixture should be findable"
    assert row.id not in {hit.id for hit in during}


def test_exclusion_does_not_delete_or_persist(memory):
    """Withheld for one run only. The next question sees it again."""
    session, row = memory
    _find(session, Ablation(exclude_memory_ids=frozenset({row.id})))

    after = _find(session)

    assert row.id in {hit.id for hit in after}
    assert session.get(Memory, row.id) is not None


def test_skip_filters_reports_that_it_did(memory):
    """An ablated run must never look like a normal one."""
    session, _ = memory

    found = retrieval.search(
        session, USER, "quarterly zephyr reconciliation",
        now=NOW,
        since=NOW - timedelta(days=1),
        apps=["slack"],
        cuts=Ablation(skip_filters=True),
    )

    assert "all filters (ablation)" in found.filters_not_applied
    # The filters it was told to apply are gone, not merely widened.
    assert found.filters_applied["since"] is None
    assert found.filters_applied["apps"] is None


def test_disabled_stage_is_reported_not_silently_skipped():
    cuts = Ablation(disable_stages=frozenset({"recall"}))

    assert cuts.allows("recall") is False
    assert cuts.allows("draft") is True
    assert cuts.active is True


def test_parse_tolerates_missing_and_empty_payloads():
    assert Ablation.parse(None).active is False
    assert Ablation.parse({}).active is False

    parsed = Ablation.parse(
        {"exclude_memory_ids": ["00000000-0000-0000-0000-0000000000aa"],
         "skip_filters": True}
    )

    assert parsed.skip_filters is True
    assert len(parsed.exclude_memory_ids) == 1
    assert parsed.as_dict()["skip_filters"] is True
