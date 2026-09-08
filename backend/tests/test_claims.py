"""Claim identity, and what has to be true before a claim is stored.

Two properties matter most here. A claim that cannot be traced to a dictation
is refused rather than stored weakly, and the same claim said twice becomes
one better-corroborated belief rather than two.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete

from app.config import settings
from app.db.session import SessionLocal
from app.models.episode import Episode
from app.models.event import Event
from app.models.memory import Memory, MemoryEvidence
from app.schemas.extraction import MemoryCandidateOut
from app.services import claims

USER = settings.default_user_id
START = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)


@pytest.fixture
def world():
    db = SessionLocal()
    _clear(db)

    episode = Episode(
        id=uuid.uuid4(),
        user_id=USER,
        started_at=START,
        ended_at=START + timedelta(minutes=20),
        event_count=0,
        status="closed",
    )
    db.add(episode)
    db.flush()
    yield db, episode

    _clear(db)
    db.close()


def _clear(db) -> None:
    db.execute(delete(MemoryEvidence).where(MemoryEvidence.user_id == USER))
    db.execute(delete(Memory).where(Memory.user_id == USER))
    db.execute(delete(Event).where(Event.user_id == USER))
    db.execute(delete(Episode).where(Episode.user_id == USER))
    db.commit()


def event(db, episode, text, minutes=0, app="slack"):
    row = Event(
        id=uuid.uuid4(),
        user_id=USER,
        occurred_at=START + timedelta(minutes=minutes),
        ingested_at=START,
        app=app,
        context_hash="ctx",
        raw_asr=text.lower(),
        formatted_text=text,
        committed_text=text,
        episode_id=episode.id,
        ingest_status="pending",
    )
    db.add(row)
    db.flush()
    return row


def candidate(content, excerpt, indexes, *, basis="explicit", subject=None,
              kind="decision"):
    return MemoryCandidateOut(
        content=content,
        type=kind,
        basis=basis,
        subject=subject,
        source_indexes=indexes,
        excerpt=excerpt,
    )


# --- identity ---------------------------------------------------------------


def test_the_same_claim_has_the_same_key_wherever_it_was_said():
    """Slack and a notes app must reinforce one belief, not make two."""
    a = claims.claim_key("Pro tier", "The Pro tier is priced at ₹299.")
    b = claims.claim_key("Pro tier", "the pro tier is priced at ₹299.")
    assert a == b


def test_a_different_subject_is_a_different_claim():
    a = claims.claim_key("Priya", "Approved the vendor contract.")
    b = claims.claim_key("Pranav", "Approved the vendor contract.")
    assert a != b


def test_a_changed_number_is_detected_as_a_different_value():
    """Similarity must never merge a price change into the price it replaced."""
    assert claims.values_in("The Pro tier is ₹299.") != claims.values_in(
        "The Pro tier is ₹349."
    )


def test_the_same_number_written_differently_still_matches():
    assert claims.values_in("₹299 for Pro") == claims.values_in("Pro is 299")


def test_half_life_depends_on_the_kind_of_claim():
    assert claims.half_life_days("preference") > claims.half_life_days("fact")
    assert claims.half_life_days("fact") > claims.half_life_days("decision")


# --- guards -----------------------------------------------------------------


def test_a_claim_with_no_resolvable_source_is_refused(world):
    db, episode = world
    source = event(db, episode, "We decided ₹299 for the Pro tier.")

    outcome = claims.persist(
        db, episode, candidate("Something.", "anything", [9]), [source]
    )
    assert outcome == "refused"
    assert db.query(Memory).count() == 0


def test_an_excerpt_that_is_not_a_quotation_downgrades_the_claim(world):
    """A model that paraphrases while claiming to quote is not restating."""
    db, episode = world
    source = event(db, episode, "We decided ₹299 for the Pro tier.")

    claims.persist(
        db,
        episode,
        candidate("Pro tier costs ₹299.", "we agreed on two ninety nine", [1]),
        [source],
    )

    memory = db.query(Memory).one()
    assert memory.status == "candidate"
    evidence = db.query(MemoryEvidence).one()
    assert "₹299" in evidence.excerpt


def test_a_quotation_straddling_two_dictations_is_rejected(world):
    """A span across the join of two utterances is something nobody said."""
    db, episode = world
    first = event(db, episode, "We decided ₹299.", minutes=0)
    second = event(db, episode, "Priya signs it off.", minutes=1)

    claims.persist(
        db,
        episode,
        candidate("Priya approved ₹299.", "₹299. Priya signs", [1, 2]),
        [first, second],
    )
    assert db.query(Memory).one().status == "candidate"


def test_an_explicit_claim_is_citable_at_once(world):
    db, episode = world
    source = event(db, episode, "We decided ₹299 for the Pro tier.")

    claims.persist(
        db, episode, candidate("We decided ₹299 for the Pro tier.",
                               "We decided ₹299 for the Pro tier.", [1]),
        [source],
    )
    assert db.query(Memory).one().status == "active"


def test_an_inferred_claim_waits(world):
    db, episode = world
    source = event(db, episode, "We decided ₹299 for the Pro tier.")

    claims.persist(
        db,
        episode,
        candidate("Pricing is settled.", "We decided ₹299", [1], basis="inferred"),
        [source],
    )
    assert db.query(Memory).one().status == "candidate"


# --- belief -----------------------------------------------------------------


def test_saying_something_twice_reinforces_one_belief(world):
    db, episode = world
    first = event(db, episode, "We decided ₹299 for the Pro tier.", minutes=0)
    second = event(db, episode, "We decided ₹299 for the Pro tier.", minutes=40)

    spec = candidate(
        "The Pro tier is ₹299.", "We decided ₹299 for the Pro tier.", [1]
    )
    assert claims.persist(db, episode, spec, [first]) == "created"

    spec.source_indexes = [1]
    assert claims.persist(db, episode, spec, [second]) == "reinforced"

    memory = db.query(Memory).one()
    assert memory.observation_count == 2
    assert memory.alpha > 2.0


def test_the_same_dictation_twice_does_not_inflate_a_belief(world):
    """Re-running the pipeline must not manufacture confidence."""
    db, episode = world
    source = event(db, episode, "We decided ₹299 for the Pro tier.")
    spec = candidate("The Pro tier is ₹299.", "We decided ₹299", [1])

    claims.persist(db, episode, spec, [source])
    before = db.query(Memory).one().alpha
    claims.persist(db, episode, spec, [source])

    memory = db.query(Memory).one()
    assert memory.alpha == before
    assert memory.observation_count == 1


def test_a_claim_from_two_apps_becomes_one_memory(world):
    """The whole point of dropping conversation-scoped identity."""
    db, episode = world
    on_slack = event(db, episode, "We decided ₹299 for the Pro tier.", app="slack")
    in_notes = event(
        db, episode, "We decided ₹299 for the Pro tier.", minutes=20, app="notion"
    )

    spec = candidate("The Pro tier is ₹299.", "We decided ₹299", [1])
    claims.persist(db, episode, spec, [on_slack])
    claims.persist(db, episode, spec, [in_notes])

    assert db.query(Memory).count() == 1
    assert db.query(MemoryEvidence).count() == 2


def test_an_inferred_claim_becomes_citable_once_stated_outright(world):
    db, episode = world
    hinted = event(db, episode, "Pricing looks settled at ₹299.", minutes=0)
    stated = event(db, episode, "The Pro tier is ₹299.", minutes=30)

    claims.persist(
        db,
        episode,
        candidate("The Pro tier is ₹299.", "Pricing looks settled at ₹299.",
                  [1], basis="inferred"),
        [hinted],
    )
    assert db.query(Memory).one().status == "candidate"

    claims.persist(
        db,
        episode,
        candidate("The Pro tier is ₹299.", "The Pro tier is ₹299.", [1]),
        [stated],
    )
    assert db.query(Memory).one().status == "active"


def test_currency_comes_from_the_dictation_not_the_episode(world):
    """An episode can be two hours wide; when the user spoke is what counts."""
    db, episode = world
    source = event(db, episode, "We decided ₹299 for the Pro tier.", minutes=5)

    claims.persist(
        db, episode, candidate("The Pro tier is ₹299.", "We decided ₹299", [1]),
        [source],
    )

    memory = db.query(Memory).one()
    assert memory.last_reinforced_at == source.occurred_at
    assert memory.last_reinforced_at != episode.ended_at


def test_evidence_points_at_the_event_and_the_episode(world):
    """Provenance must reach the exact dictation, not just the stretch."""
    db, episode = world
    source = event(db, episode, "We decided ₹299 for the Pro tier.")

    claims.persist(
        db, episode, candidate("The Pro tier is ₹299.", "We decided ₹299", [1]),
        [source],
    )

    evidence = db.query(MemoryEvidence).one()
    assert evidence.event_id == source.id
    assert evidence.episode_id == episode.id
    assert evidence.stance == "supports"
