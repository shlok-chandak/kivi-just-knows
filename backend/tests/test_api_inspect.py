"""The inspection surface: traces, provenance, belief, and what was ignored.

These endpoints exist to be trusted, so the tests are mostly about what they
must never do -- show a sensitive candidate, replay an ingest, or report a
confidence computed differently from the one used to rank.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete

from app.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.event import Event
from app.models.memory import Memory, MemoryEvidence
from app.models.rejected import RejectedCandidate
from app.models.trace import Trace, TraceStep
from app.services import asking, parse, tracing
from app.schemas.query import QuerySpec

client = TestClient(app)

# The endpoints answer for the configured user, so the fixtures belong to it.
# Safe because the whole suite runs against a separate database.
USER = settings.default_user_id

NOW = datetime(2026, 6, 17, 9, 0, tzinfo=timezone.utc)


@pytest.fixture
def db():
    session = SessionLocal()
    made: dict[type, list[uuid.UUID]] = {}

    def track(row):
        session.add(row)
        session.flush()
        made.setdefault(type(row), []).append(row.id)
        return row

    yield session, track

    session.rollback()
    for model in (TraceStep, Trace, RejectedCandidate, MemoryEvidence, Memory, Event):
        ids = made.get(model)
        if ids:
            session.execute(delete(model).where(model.id.in_(ids)))
    session.commit()
    session.close()


def _event(track, **overrides):
    base = dict(
        user_id=USER,
        occurred_at=NOW - timedelta(days=3),
        ingested_at=NOW - timedelta(days=3),
        raw_asr="rate is eighteen fifty",
        formatted_text="Rate is 18.50.",
        app="slack",
    )
    base.update(overrides)
    return track(Event(**base))


def _memory(track, **overrides):
    base = dict(
        user_id=USER,
        type="fact",
        content="The agreed rate is 18.50.",
        claim_key=f"test:{uuid.uuid4()}",
        status="active",
        alpha=3.0,
        beta=1.0,
        observation_count=2,
        last_reinforced_at=NOW - timedelta(days=3),
    )
    base.update(overrides)
    return track(Memory(**base))


# --- ignored ---------------------------------------------------------------


def test_ignored_lists_rules_and_counts(db):
    session, track = db
    track(
        RejectedCandidate(
            user_id=USER,
            rejection_rule="transient",
            rationale="not durable",
            candidate={"content": "brb"},
        )
    )
    session.commit()

    body = client.get("/ignored").json()

    assert body["by_rule"].get("transient", 0) >= 1
    assert body["total"] >= 1


def test_sensitive_candidate_is_never_returned(db):
    """The content is the thing being refused. Showing it would undo that."""
    session, track = db
    track(
        RejectedCandidate(
            user_id=USER,
            rejection_rule="sensitive_category",
            rationale="looked like a credential",
            app="notes",
            occurred_at=NOW,
            candidate={"content": "the password is hunter2"},
        )
    )
    session.commit()

    rows = client.get("/ignored?rule=sensitive_category").json()["ignored"]
    mine = [row for row in rows if row["rationale"] == "looked like a credential"]

    assert mine, "the refusal should be visible"
    assert mine[0]["withheld"] is True
    assert mine[0]["candidate"] is None
    # The gap is still visible: when and where, never what.
    assert mine[0]["app"] == "notes"


# --- provenance ------------------------------------------------------------


def test_provenance_returns_the_users_own_words(db):
    session, track = db
    event = _event(track, formatted_text="Rate is 18.50, confirmed with them.")
    memory = _memory(track)
    track(
        MemoryEvidence(
            user_id=USER,
            memory_id=memory.id,
            event_id=event.id,
            stance="supports",
            weight=1.0,
            excerpt="Rate is 18.50",
            observed_at=event.occurred_at,
        )
    )
    session.commit()

    body = client.get(f"/memories/{memory.id}/provenance").json()

    assert body["count"] == 1
    row = body["evidence"][0]
    assert row["excerpt"] == "Rate is 18.50"
    # The excerpt is a span of what was said, not a paraphrase of it.
    assert row["excerpt"] in row["said"]
    assert row["stance"] == "supports"


def test_provenance_404s_for_an_unknown_memory():
    assert client.get(f"/memories/{uuid.uuid4()}/provenance").status_code == 404


# --- belief ----------------------------------------------------------------


def test_belief_reports_the_same_confidence_used_to_rank(db):
    session, track = db
    memory = _memory(track)
    session.commit()

    body = client.get(
        f"/memories/{memory.id}/belief",
        headers={"X-Kivi-Now": NOW.isoformat()},
    ).json()

    # alpha / (alpha + beta), the generated column -- not recomputed here.
    assert body["confidence"]["value"] == pytest.approx(0.75, abs=1e-4)
    assert body["confidence"]["alpha"] == 3.0
    assert body["currency"]["half_life_days"] == 90


def test_currency_falls_with_age_and_confidence_does_not(db):
    """Two numbers, not one. Only currency moves with the clock."""
    session, track = db
    memory = _memory(track)
    session.commit()

    fresh = client.get(
        f"/memories/{memory.id}/belief",
        headers={"X-Kivi-Now": NOW.isoformat()},
    ).json()
    later = client.get(
        f"/memories/{memory.id}/belief",
        headers={"X-Kivi-Now": (NOW + timedelta(days=200)).isoformat()},
    ).json()

    assert later["currency"]["value"] < fresh["currency"]["value"]
    assert later["confidence"]["value"] == fresh["confidence"]["value"]


def test_belief_names_the_claim_that_replaced_it(db):
    session, track = db
    newer = _memory(track, content="The agreed rate is 19.00.")
    older = _memory(
        track,
        content="The agreed rate is 18.50.",
        status="superseded",
        superseded_by=newer.id,
    )
    session.commit()

    body = client.get(f"/memories/{older.id}/belief").json()

    assert body["superseded_by"]["content"] == "The agreed rate is 19.00."


# --- traces ----------------------------------------------------------------


def test_trace_detail_lists_stages_in_order(db):
    session, track = db
    recorder = tracing.start_query_trace(session, user_id=USER, request="what rate")
    recorder.step("parse", decision="recall: rate")
    recorder.step("plan", decision="recall", rationale="answer from history")
    recorder.close("answered", final_output="18.50")
    session.commit()

    body = client.get(f"/traces/{recorder.id}").json()
    session.execute(delete(TraceStep).where(TraceStep.trace_id == recorder.id))
    session.execute(delete(Trace).where(Trace.id == recorder.id))
    session.commit()

    assert body["input"] == "what rate"
    assert body["outcome"] == "answered"
    assert [step["stage"] for step in body["steps"]] == ["parse", "plan"]
    assert body["steps"][1]["rationale"] == "answer from history"


def test_trace_detail_404s_for_an_unknown_trace():
    assert client.get(f"/traces/{uuid.uuid4()}").status_code == 404


def test_ingest_traces_cannot_be_replayed(db):
    """Replaying an ingest would extract the dictation twice and write beliefs."""
    session, _ = db
    recorder = tracing.start_ingest_trace(
        session, user_id=USER, subject_key=str(uuid.uuid4())
    )
    recorder.close("answered")
    session.commit()

    response = client.post(f"/traces/{recorder.id}/replay")
    session.execute(delete(Trace).where(Trace.id == recorder.id))
    session.commit()

    assert response.status_code == 400
    assert "query" in response.json()["detail"].lower()


# --- the pieces that make tracing possible ---------------------------------


def test_parsed_still_unpacks_as_a_pair():
    """Call sites written before usage was carried must keep working."""
    spec, by_model = parse.Parsed(QuerySpec(intent="recall", topic="rate"), True)

    assert spec.topic == "rate"
    assert by_model is True


def test_step_summary_drops_the_bulky_parts():
    """A trace should explain a result, not store a second copy of it."""
    small = asking._summarise(
        {
            "answered": True,
            "answer": "a" * 5000,
            "citations": [{"text": "x"}, {"text": "y"}],
        }
    )

    assert small == {"answered": True, "citations_count": 2}
