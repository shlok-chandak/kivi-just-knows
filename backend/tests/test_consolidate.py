"""Consolidation: one call per closed episode, and what happens either side.

The provider is always stubbed. These tests assert which branch was taken and
what was written, never what a model returns.
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
from app.models.rejected import RejectedCandidate
from app.schemas.consolidation import ConsolidationOut
from app.schemas.extraction import MemoryCandidateOut
from app.services import consolidate as consolidate_module
from app.services.consolidate import RULE_NOTHING_TO_EXTRACT, consolidate_episode
from app.services.consolidate import SENSITIVE_ON_REVIEW
from tests.conftest import TEST_USER

USER = TEST_USER
START = datetime(2026, 9, 4, 10, 0, tzinfo=timezone.utc)


class _Stub:
    """Returns a fixed structured result, and counts the calls."""

    def __init__(self, value):
        self.value = value
        self.calls = 0

    def structured(self, **kwargs):
        from app.llm.client import Completion, Usage

        self.calls += 1
        self.prompt = kwargs["prompt"]
        return Completion(
            value=self.value,
            usage=Usage(
                model="stub",
                input_tokens=10,
                output_tokens=5,
                latency_ms=1,
                cost_usd=0.0,
            ),
        )


def out(memories=(), title="Stub title", summary="Stub summary."):
    return ConsolidationOut(
        title=title,
        summary=summary,
        topic_tags=["stub"],
        memories=list(memories),
    )


def claim(content, excerpt, indexes=(1,)):
    return MemoryCandidateOut(
        content=content,
        type="decision",
        basis="explicit",
        subject=None,
        source_indexes=list(indexes),
        excerpt=excerpt,
    )


@pytest.fixture
def world(monkeypatch):
    db = SessionLocal()
    _clear(db)

    episode = Episode(
        id=uuid.uuid4(),
        user_id=USER,
        started_at=START,
        ended_at=START + timedelta(minutes=30),
        event_count=0,
        status="closed",
    )
    db.add(episode)
    db.flush()

    def install(result):
        stub = _Stub(result)
        monkeypatch.setattr(consolidate_module, "get_client", lambda: stub)
        return stub

    yield db, episode, install

    _clear(db)
    db.close()


def _clear(db) -> None:
    db.execute(delete(MemoryEvidence).where(MemoryEvidence.user_id == USER))
    db.execute(delete(Memory).where(Memory.user_id == USER))
    db.execute(delete(RejectedCandidate).where(RejectedCandidate.user_id == USER))
    db.execute(delete(Event).where(Event.user_id == USER))
    db.execute(delete(Episode).where(Episode.user_id == USER))
    db.commit()


def event(db, episode, text, minutes=0, app="slack", context="ctx"):
    row = Event(
        id=uuid.uuid4(),
        user_id=USER,
        occurred_at=START + timedelta(minutes=minutes),
        ingested_at=START,
        app=app,
        context_hash=context,
        raw_asr=text.lower(),
        formatted_text=text,
        committed_text=text,
        episode_id=episode.id,
        ingest_status="pending",
    )
    db.add(row)
    db.flush()
    return row


# --- branches that cost nothing ---------------------------------------------


def test_an_empty_episode_never_reaches_a_model(world):
    db, episode, install = world
    stub = install(out())

    result = consolidate_episode(db, episode.id)

    assert stub.calls == 0
    assert result["summary_status"] == "skipped"
    assert episode.summary is None


def test_small_talk_is_kept_verbatim_without_a_call(world):
    db, episode, install = world
    stub = install(out())
    event(db, episode, "Morning.", minutes=0)
    event(db, episode, "All good here.", minutes=2)

    result = consolidate_episode(db, episode.id)

    assert stub.calls == 0
    assert result["summary_status"] == "verbatim"
    assert episode.summary == "Morning. All good here."


def test_a_skipped_episode_is_written_to_the_ignore_log(world):
    db, episode, install = world
    install(out())
    event(db, episode, "Testing testing one two three.")

    consolidate_episode(db, episode.id)

    rejected = db.query(RejectedCandidate).one()
    assert rejected.rejection_rule == RULE_NOTHING_TO_EXTRACT
    assert rejected.episode_id == episode.id


def test_content_the_model_flags_as_sensitive_is_deleted(world):
    """The second net: what a pattern could not recognise, a reader can."""
    db, episode, install = world
    install(out([], title="Stub"))
    keep = event(db, episode, "We decided ₹299 for the Pro tier.", minutes=0)
    drop = event(db, episode, "Mum's biopsy results came back today.", minutes=4)
    event(db, episode, "Priya will confirm with the vendor.", minutes=8)

    consolidate_module.get_client().value.sensitive_indexes = [2]
    consolidate_episode(db, episode.id)

    assert db.get(Event, drop.id) is None
    assert db.get(Event, keep.id) is not None


def test_a_purge_is_logged_without_the_content(world):
    db, episode, install = world
    install(out([]))
    event(db, episode, "We decided ₹299 for the Pro tier.", minutes=0)
    event(db, episode, "Mum's biopsy results came back today.", minutes=4)
    event(db, episode, "Priya will confirm with the vendor.", minutes=8)

    consolidate_module.get_client().value.sensitive_indexes = [2]
    consolidate_episode(db, episode.id)

    purge = (
        db.query(RejectedCandidate)
        .filter(RejectedCandidate.rejection_rule == SENSITIVE_ON_REVIEW)
        .one()
    )
    assert purge.candidate is None
    assert purge.occurred_at is not None
    assert purge.app == "slack"


def test_nothing_is_purged_when_the_user_opts_in(world, monkeypatch):
    db, episode, install = world
    install(out([]))
    monkeypatch.setattr(consolidate_module.settings, "store_sensitive_content", True)
    row = event(db, episode, "Mum's biopsy results came back today.", minutes=0)
    event(db, episode, "We decided ₹299 for the Pro tier.", minutes=4)
    event(db, episode, "Priya will confirm with the vendor.", minutes=8)

    consolidate_module.get_client().value.sensitive_indexes = [1]
    consolidate_episode(db, episode.id)

    assert db.get(Event, row.id) is not None


def test_a_refused_episode_leaves_its_events_unconsolidated(world):
    """Nothing was learned, so the fast path must keep answering from them."""
    db, episode, install = world
    install(out())
    row = event(db, episode, "Testing testing one two three.")

    consolidate_episode(db, episode.id)

    db.refresh(row)
    assert row.consolidated_at is None


# --- the call ---------------------------------------------------------------


def test_a_decision_earns_a_call_and_becomes_a_memory(world):
    db, episode, install = world
    text = "We decided ₹299 for the Pro tier."
    stub = install(out([claim("The Pro tier is ₹299.", "We decided ₹299")]))
    event(db, episode, text)

    result = consolidate_episode(db, episode.id)

    assert stub.calls == 1
    assert result["created"] == 1
    assert db.query(Memory).count() == 1


def test_a_single_dictation_keeps_its_own_words_as_the_summary(world):
    """The call was made for the claims; a paraphrase of one line embeds worse."""
    db, episode, install = world
    text = "We decided ₹299 for the Pro tier."
    install(out([claim("The Pro tier is ₹299.", "We decided ₹299")]))
    event(db, episode, text)

    consolidate_episode(db, episode.id)

    assert episode.summary == text
    assert episode.summary_status == "verbatim"
    assert episode.topic_tags == ["stub"]


def test_several_dictations_get_a_generated_summary(world):
    db, episode, install = world
    install(out([claim("The Pro tier is ₹299.", "We decided ₹299")]))
    event(db, episode, "We decided ₹299 for the Pro tier.", minutes=0)
    event(db, episode, "Priya will confirm with the vendor on Monday.", minutes=4)
    event(db, episode, "The renewal quote goes out on Thursday.", minutes=9)

    consolidate_episode(db, episode.id)

    assert episode.summary == "Stub summary."
    assert episode.summary_status == "generated"


def test_consolidation_hands_the_events_over_to_their_memories(world):
    db, episode, install = world
    install(out([claim("The Pro tier is ₹299.", "We decided ₹299")]))
    row = event(db, episode, "We decided ₹299 for the Pro tier.")

    consolidate_episode(db, episode.id)

    db.refresh(row)
    assert row.consolidated_at is not None


def test_finding_nothing_is_recorded_rather_than_passed_over(world):
    """A model that looked and found nothing is a better record than a guess."""
    db, episode, install = world
    install(out([]))
    event(db, episode, "We decided ₹299 for the Pro tier.")

    consolidate_episode(db, episode.id)

    rejected = db.query(RejectedCandidate).one()
    assert rejected.rejection_rule == RULE_NOTHING_TO_EXTRACT
    assert "model" in rejected.rationale


# --- what the model is shown ------------------------------------------------


def test_the_prompt_shows_sittings_rather_than_a_flat_list(world):
    """Structure that cost nothing must not be thrown away before the call."""
    db, episode, install = world
    stub = install(out())
    event(db, episode, "We decided ₹299 for the Pro tier.", minutes=0,
          app="slack", context="a")
    event(db, episode, "Priya will confirm with the vendor.", minutes=2,
          app="slack", context="a")
    event(db, episode, "Writing up the pricing rationale now.", minutes=25,
          app="notion", context="b")

    consolidate_episode(db, episode.id)

    assert "Sitting 1" in stub.prompt
    assert "Sitting 2" in stub.prompt
    assert "notion" in stub.prompt


def test_dictations_are_numbered_across_the_whole_episode(world):
    """Restarting per sitting would make a cited number ambiguous."""
    db, episode, install = world
    stub = install(out())
    event(db, episode, "We decided ₹299 for the Pro tier.", minutes=0, context="a")
    event(db, episode, "Priya will confirm with the vendor.", minutes=2, context="a")
    event(db, episode, "Writing up the pricing rationale now.", minutes=40,
          context="b")

    consolidate_episode(db, episode.id)

    assert "3. Writing up the pricing rationale now." in stub.prompt


def test_a_missing_episode_is_an_error(world):
    db, _, install = world
    install(out())
    with pytest.raises(ValueError):
        consolidate_episode(db, uuid.uuid4())


def test_a_claim_drawn_from_purged_content_is_dropped(world):
    """Deleting the dictation is useless if the claim keeps the detail."""
    db, episode, install = world
    install(out([claim("Account ending 3312 was debited ₹40,000.",
                       "transfer ₹40,000", indexes=[2])]))
    event(db, episode, "We decided ₹299 for the Pro tier.", minutes=0)
    event(db, episode, "Transfer ₹40,000 from the account ending 3312.", minutes=4)
    event(db, episode, "Priya will confirm with the vendor.", minutes=8)

    consolidate_module.get_client().value.sensitive_indexes = [2]
    result = consolidate_episode(db, episode.id)

    assert result["created"] == 0
    assert db.query(Memory).count() == 0


def test_a_purge_does_not_renumber_the_remaining_claims(world):
    """Claims cite by position, so removing an entry would shift every index."""
    db, episode, install = world
    install(out([claim("Priya will confirm with the vendor.",
                       "Priya will confirm with the vendor.", indexes=[3])]))
    event(db, episode, "We decided ₹299 for the Pro tier.", minutes=0)
    event(db, episode, "Transfer ₹40,000 from the account ending 3312.", minutes=4)
    third = event(db, episode, "Priya will confirm with the vendor.", minutes=8)

    consolidate_module.get_client().value.sensitive_indexes = [2]
    consolidate_episode(db, episode.id)

    evidence = db.query(MemoryEvidence).one()
    assert evidence.event_id == third.id
