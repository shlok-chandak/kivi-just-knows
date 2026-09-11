"""Listing and deleting what is remembered.

The list is the screen a person uses to decide whether to trust the system,
so the properties that matter are about honesty: history is not mixed into
the present without being asked for, a replaced belief says what replaced
it, and deleting one really removes it rather than hiding it.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.embedding import Embedding
from app.models.memory import Memory, MemoryEvidence
from app.services import embed

client = TestClient(app)

USER = settings.default_user_id
NOW = datetime(2026, 6, 17, 9, 0, tzinfo=timezone.utc)
AS_OF = {"x-kivi-now": NOW.isoformat()}

# Distinctive, so the assertions filter to this test's rows and not the
# whole database, which the suite shares.
NEEDLE = "zephyr reconciliation"


@pytest.fixture
def rows():
    session = SessionLocal()
    made: list[uuid.UUID] = []

    def add(content, status="active", days=3, **extra):
        row = Memory(
            user_id=USER,
            type=extra.pop("type", "fact"),
            content=content,
            claim_key=f"test:{uuid.uuid4()}",
            status=status,
            alpha=3.0,
            beta=1.0,
            observation_count=2,
            last_reinforced_at=NOW - timedelta(days=days),
            **extra,
        )
        session.add(row)
        session.flush()
        made.append(row.id)
        return row

    yield session, add

    session.rollback()
    session.execute(delete(MemoryEvidence).where(MemoryEvidence.memory_id.in_(made)))
    session.execute(
        delete(Embedding).where(
            Embedding.object_type == "memory", Embedding.object_id.in_(made)
        )
    )
    session.execute(delete(Memory).where(Memory.id.in_(made)))
    session.commit()
    session.close()


def _listing(**params):
    params.setdefault("q", NEEDLE)
    return client.get("/memories", params=params, headers=AS_OF).json()


# --- what the list shows ----------------------------------------------------


def test_history_is_not_mixed_into_the_present_by_default(rows):
    session, add = rows
    live = add(f"The {NEEDLE} runs on Tuesdays.")
    old = add(f"The {NEEDLE} runs on Mondays.", status="superseded")
    session.commit()

    ids = {row["id"] for row in _listing()["memories"]}
    assert str(live.id) in ids
    assert str(old.id) not in ids


def test_history_is_available_when_asked_for(rows):
    session, add = rows
    live = add(f"The {NEEDLE} runs on Tuesdays.")
    old = add(f"The {NEEDLE} runs on Mondays.", status="superseded")
    session.commit()

    only = {row["id"] for row in _listing(status="superseded")["memories"]}
    assert only == {str(old.id)}

    both = {row["id"] for row in _listing(status="all")["memories"]}
    assert both >= {str(live.id), str(old.id)}


def test_a_replaced_belief_names_what_replaced_it(rows):
    session, add = rows
    live = add(f"The {NEEDLE} runs on Tuesdays.")
    old = add(
        f"The {NEEDLE} runs on Mondays.", status="superseded", superseded_by=live.id
    )
    session.commit()

    row = _listing(status="superseded")["memories"][0]
    assert row["id"] == str(old.id)
    assert row["superseded_by"]["content"] == f"The {NEEDLE} runs on Tuesdays."


def test_confidence_and_currency_both_travel(rows):
    """One number cannot distinguish a solid old claim from a shaky new one."""
    session, add = rows
    add(f"The {NEEDLE} runs on Tuesdays.", days=400)
    session.commit()

    row = _listing()["memories"][0]
    assert row["confidence"] == pytest.approx(0.75, abs=0.01)
    assert row["currency"] < row["confidence"]
    assert row["stale"] is True


def test_an_unknown_status_is_refused_rather_than_guessed(rows):
    assert client.get("/memories", params={"status": "alive"}).status_code == 422


def test_counts_cover_every_status(rows):
    session, add = rows
    add(f"The {NEEDLE} runs on Tuesdays.")
    add(f"The {NEEDLE} runs on Mondays.", status="superseded")
    session.commit()

    counts = _listing(status="all")["counts"]
    assert counts.get("active", 0) >= 1
    assert counts.get("superseded", 0) >= 1


def test_paging_reports_the_whole_size_not_the_page(rows):
    session, add = rows
    for day in range(5):
        add(f"The {NEEDLE} item {day} is open.", days=day + 1)
    session.commit()

    page = _listing(limit=2)
    assert len(page["memories"]) == 2
    assert page["total"] >= 5


# --- deleting ---------------------------------------------------------------


def test_deleting_removes_the_belief_and_its_vector(rows):
    """A vector left behind keeps the text findable through its excerpt."""
    session, add = rows
    doomed = add(f"The {NEEDLE} runs on Tuesdays.")
    session.commit()
    embed.store(session, USER, [("memory", doomed.id, doomed.content)])
    session.commit()

    assert client.delete(f"/memories/{doomed.id}").status_code == 200

    # Queried rather than fetched by identity: the row is gone underneath
    # this session, and get() on a stale identity-map entry raises instead
    # of reporting the absence we are asserting.
    session.expunge_all()
    assert session.scalar(select(Memory).where(Memory.id == doomed.id)) is None
    assert not list(
        session.scalars(
            select(Embedding).where(
                Embedding.object_type == "memory", Embedding.object_id == doomed.id
            )
        )
    )


def test_deleting_a_replacement_leaves_no_broken_link(rows):
    """The link nulls on delete, so history loses an address rather than
    gaining a wrong one."""
    session, add = rows
    live = add(f"The {NEEDLE} runs on Tuesdays.")
    old = add(
        f"The {NEEDLE} runs on Mondays.", status="superseded", superseded_by=live.id
    )
    session.commit()

    client.delete(f"/memories/{live.id}")
    session.expire_all()

    assert session.get(Memory, old.id).superseded_by is None


def test_deleting_an_unknown_memory_is_a_404(rows):
    assert client.delete(f"/memories/{uuid.uuid4()}").status_code == 404
