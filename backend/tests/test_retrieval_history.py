"""Reaching history without mistaking it for the present.

A store that keeps superseded beliefs and cannot retrieve them is keeping
them for nobody: "what was the price before we raised it" is a real
question. The danger was never that history is reachable, it is that
history is read as current -- so these tests hold both halves at once.

Trigram-findable wording throughout, so the assertions do not depend on an
embedding row existing for the fixture.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.config import settings
from app.db.session import SessionLocal
from app.models.memory import Memory
from app.services import recall, retrieval

USER = settings.default_user_id
NOW = datetime(2026, 6, 17, 9, 0, tzinfo=timezone.utc)

QUERY = "zephyr subscription price"
CURRENT = "The zephyr subscription price is 349 a month."
REPLACED = "The zephyr subscription price is 299 a month."


def _memory(session, content, status, days_ago, valid_until=None):
    row = Memory(
        user_id=USER,
        type="fact",
        content=content,
        claim_key=f"test:{uuid.uuid4()}",
        status=status,
        alpha=3.0,
        beta=1.0,
        observation_count=2,
        last_reinforced_at=NOW - timedelta(days=days_ago),
        valid_until=valid_until,
    )
    session.add(row)
    return row


@pytest.fixture
def chain():
    """One live belief and the one it replaced."""
    session = SessionLocal()
    live = _memory(session, CURRENT, "active", days_ago=2)
    # As the pipeline writes it: valid_until records when it stopped being
    # true, which is also what makes it look "expired" to the ranker.
    dead = _memory(
        session, REPLACED, "superseded", days_ago=40,
        valid_until=NOW - timedelta(days=2),
    )
    session.commit()
    yield session, live, dead
    session.execute(delete(Memory).where(Memory.id.in_([live.id, dead.id])))
    session.commit()
    session.close()


def _search(session, **kwargs):
    return retrieval.search_memories(session, USER, QUERY, now=NOW, **kwargs)


def test_history_is_excluded_unless_it_is_asked_for(chain):
    session, live, dead = chain
    found = {hit.id for hit in _search(session)}
    assert live.id in found
    assert dead.id not in found


def test_history_comes_back_when_it_is_asked_for(chain):
    session, live, dead = chain
    found = {hit.id for hit in _search(session, include_superseded=True)}
    assert {live.id, dead.id} <= found


def test_a_replaced_belief_is_not_dropped_for_looking_expired(chain):
    """The bug that hid this one layer further down.

    `valid_until` means two things -- a commitment's deadline, and the
    moment a belief stopped being true. Every superseded row has the
    second, so an expiry check applied to them removes all of history.
    """
    session, _, dead = chain
    hits = _search(session, include_superseded=True)
    assert any(hit.id == dead.id for hit in hits)


def test_the_replaced_one_is_flagged_and_the_live_one_is_not(chain):
    session, live, dead = chain
    flags = {hit.id: hit.superseded for hit in _search(session, include_superseded=True)}
    assert flags[dead.id] is True
    assert flags[live.id] is False


def test_the_model_is_told_which_source_is_history(chain):
    """REPLACED in the rendered source is what the answer prompt acts on."""
    session, live, dead = chain
    sources = recall.to_sources(_search(session, include_superseded=True))
    rendered = {source.id: source.rendered() for source in sources}
    assert "REPLACED" in rendered[dead.id]
    assert "REPLACED" not in rendered[live.id]


def test_being_old_is_not_being_replaced(chain):
    """A belief nobody has restated in months is old, not wrong.

    This used to render as REPLACED, which told the model a current belief
    was history while actual history never reached it at all.
    """
    session = chain[0]
    ancient = _memory(session, "The zephyr retro is on Thursdays.", "active", days_ago=400)
    session.commit()
    try:
        hits = retrieval.search_memories(
            session, USER, "zephyr retro Thursdays", now=NOW, include_superseded=True
        )
        hit = next(h for h in hits if h.id == ancient.id)
        assert hit.stale is True, "old enough to be worth dating"
        assert hit.superseded is False
        assert "REPLACED" not in recall.to_sources([hit])[0].rendered()
    finally:
        session.execute(delete(Memory).where(Memory.id == ancient.id))
        session.commit()


def test_relevance_leads_and_a_live_belief_wins_a_tie(chain):
    """Ranking all history last was tried and buried a 0.78 match under a 0.25 one."""
    live = retrieval.Candidate(
        kind="memory", id=uuid.uuid4(), text="a", occurred_at=NOW,
        similarity=0.5, score=0.5, superseded=False,
    )
    dead_close = retrieval.Candidate(
        kind="memory", id=uuid.uuid4(), text="b", occurred_at=NOW,
        similarity=0.9, score=0.9, superseded=True,
    )
    dead_tied = retrieval.Candidate(
        kind="memory", id=uuid.uuid4(), text="c", occurred_at=NOW,
        similarity=0.5, score=0.5, superseded=True,
    )

    ordered = sorted([live, dead_tied, dead_close], key=retrieval._order)

    assert ordered[0] is dead_close, "a far better match must not be buried"
    assert ordered[1] is live, "on equal scores the live belief leads"
    assert ordered[2] is dead_tied


def test_the_whole_search_reaches_history(chain):
    """The end-to-end path, which is where the exclusion actually lived."""
    session, _, dead = chain
    found = retrieval.search(session, USER, QUERY, now=NOW)
    assert any(c.id == dead.id and c.superseded for c in found.candidates)
