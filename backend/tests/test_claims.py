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
from app.services import claims, embed
from tests.conftest import TEST_USER

USER = TEST_USER
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


@pytest.mark.parametrize(
    "text",
    [
        "We decided to keep the price",  # dec
        "the pitch deck is ready",       # dec
        "that was a good decision",      # dec
        "a 40 percent margin on that",   # mar
        "marching orders from Priya",    # mar
        "maybe we ship on Friday",       # may
        "the junior dev owns it",        # jun
        "augment the onboarding flow",   # aug
        "the april_config file",         # apr, but not a word on its own
    ],
)
def test_a_word_that_merely_starts_like_a_month_is_not_a_date(text):
    """A prefix match read "decided" as December and "deck" as December.

    The cost was not cosmetic: a sentence carrying a fake month held a value
    the sentence it restated did not, so the two looked like a change of mind.
    """
    months = {value for value in claims.values_in(text) if not value.isdigit()}
    assert months == set()


@pytest.mark.parametrize(
    "text,expected",
    [
        ("delayed until November", "november"),
        ("moved to Oct", "oct"),
        ("due 15 Mar", "mar"),
        ("shipping in September", "september"),
        ("sept at the earliest", "sept"),
    ],
)
def test_a_real_month_is_still_a_date(text, expected):
    assert expected in claims.values_in(text)


def test_a_restatement_that_says_decided_holds_the_same_values():
    """The failure this fix exists for: same price, one sentence saying so."""
    assert claims.values_in("We decided pricing stays at 349") == claims.values_in(
        "Pricing is 349 a month"
    )


def test_a_rewrite_is_checked_against_the_same_facts_a_claim_is():
    """One definition. Two copies of this pattern drifted once already."""
    from app.services import restyle

    text = "We decided on 349 by November"
    assert restyle.facts_in(text) == set(claims.values_in(text))


def test_half_life_depends_on_the_kind_of_claim():
    assert claims.half_life_days("decision") > claims.half_life_days("fact")
    assert claims.half_life_days("fact") > claims.half_life_days("commitment")


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


# --- supersession -------------------------------------------------------------


def _index(db, memory):
    """Give a stored belief its vector.

    Consolidation does this once an episode finishes, so by the time a later
    episode contradicts a belief its vector exists. A test has to do it
    explicitly, and skipping it is why supersession can appear to work in
    isolation and never fire in the pipeline.
    """
    embed.store(db, USER, [("memory", memory.id, memory.content)])
    db.commit()


def test_a_changed_price_supersedes_the_price_it_replaced(world):
    """The point of the whole mechanism.

    Two live beliefs about one price mean the system cannot answer "what do
    we charge" -- both are true as far as it knows, and whichever ranks
    higher wins by accident.
    """
    db, episode = world
    first = event(db, episode, "The list price for the Pro tier is ₹499.", minutes=0)
    later = event(db, episode, "We dropped the Pro tier price to ₹299.", minutes=90)

    assert claims.persist(
        db,
        episode,
        candidate("The list price for the Pro tier is ₹499.",
                  "The list price for the Pro tier is ₹499.", [1]),
        [first],
    ) == "created"
    original = db.query(Memory).one()
    _index(db, original)

    outcome = claims.persist(
        db,
        episode,
        candidate("The Pro tier price is ₹299 a month.",
                  "We dropped the Pro tier price to ₹299.", [1]),
        [later],
    )

    assert outcome == "superseded"
    db.refresh(original)
    assert original.status == "superseded"
    assert original.valid_until is not None

    live = db.query(Memory).filter(Memory.status == "active").one()
    assert "299" in live.content


def test_the_replaced_belief_is_kept_rather_than_deleted(world):
    """"What was the price in June" is a different question from "what is it"."""
    db, episode = world
    first = event(db, episode, "The list price for the Pro tier is ₹499.", minutes=0)
    later = event(db, episode, "We dropped the Pro tier price to ₹299.", minutes=90)

    claims.persist(
        db, episode,
        candidate("The list price for the Pro tier is ₹499.",
                  "The list price for the Pro tier is ₹499.", [1]),
        [first],
    )
    _index(db, db.query(Memory).one())
    claims.persist(
        db, episode,
        candidate("The Pro tier price is ₹299 a month.",
                  "We dropped the Pro tier price to ₹299.", [1]),
        [later],
    )

    assert db.query(Memory).count() == 2


def test_a_claim_arriving_late_does_not_overwrite_newer_news(world):
    """Queue order is not chronological order.

    A retry or a backfill can present last month's decision after this
    month's. Trusting arrival order would let stale news win exactly when
    the queue is under stress.
    """
    db, episode = world
    recent = event(db, episode, "We dropped the Pro tier price to ₹299.", minutes=90)
    old = event(db, episode, "The list price for the Pro tier is ₹499.", minutes=0)

    claims.persist(
        db, episode,
        candidate("The Pro tier price is ₹299 a month.",
                  "We dropped the Pro tier price to ₹299.", [1]),
        [recent],
    )
    current = db.query(Memory).one()
    _index(db, current)

    claims.persist(
        db, episode,
        candidate("The list price for the Pro tier is ₹499.",
                  "The list price for the Pro tier is ₹499.", [1]),
        [old],
    )

    db.refresh(current)
    assert current.status == "active"
    stale = db.query(Memory).filter(Memory.id != current.id).one()
    assert stale.status == "superseded"


def test_the_retired_belief_names_what_replaced_it(world):
    """Knowing a belief is dead is half an answer.

    "Your price is no longer 499" is worth much less than "499 became 299",
    and only the link makes the second sayable.
    """
    db, episode = world
    first = event(db, episode, "The list price for the Pro tier is ₹499.", minutes=0)
    later = event(db, episode, "We dropped the Pro tier price to ₹299.", minutes=90)

    claims.persist(
        db, episode,
        candidate("The list price for the Pro tier is ₹499.",
                  "The list price for the Pro tier is ₹499.", [1]),
        [first],
    )
    original = db.query(Memory).one()
    _index(db, original)

    claims.persist(
        db, episode,
        candidate("The Pro tier price is ₹299 a month.",
                  "We dropped the Pro tier price to ₹299.", [1]),
        [later],
    )

    db.refresh(original)
    replacement = db.query(Memory).filter(Memory.status == "active").one()
    assert original.superseded_by == replacement.id


def test_a_claim_that_arrives_late_points_at_the_belief_that_already_held(world):
    """The losing side is the newcomer, so the newcomer carries the link."""
    db, episode = world
    recent = event(db, episode, "We dropped the Pro tier price to ₹299.", minutes=90)
    old = event(db, episode, "The list price for the Pro tier is ₹499.", minutes=0)

    claims.persist(
        db, episode,
        candidate("The Pro tier price is ₹299 a month.",
                  "We dropped the Pro tier price to ₹299.", [1]),
        [recent],
    )
    current = db.query(Memory).one()
    _index(db, current)

    claims.persist(
        db, episode,
        candidate("The list price for the Pro tier is ₹499.",
                  "The list price for the Pro tier is ₹499.", [1]),
        [old],
    )

    stale = db.query(Memory).filter(Memory.id != current.id).one()
    assert stale.superseded_by == current.id


def test_three_prices_leave_a_chain_that_can_be_walked(world):
    """499 -> 299 -> 349, readable end to end rather than three dead ends."""
    db, episode = world
    prices = [
        ("The list price for the Pro tier is ₹499.", 0),
        ("The Pro tier price is ₹299 a month.", 90),
        ("The Pro tier price is ₹349 a month.", 180),
    ]
    for content, minutes in prices:
        source = event(db, episode, content, minutes=minutes)
        claims.persist(db, episode, candidate(content, content, [1]), [source])
        for memory in db.query(Memory).all():
            _index(db, memory)

    live = db.query(Memory).filter(Memory.status == "active").one()
    assert "349" in live.content

    # Walk backwards from the oldest belief to the one in force.
    oldest = db.query(Memory).filter(Memory.content.like("%499%")).one()
    walked = [oldest]
    while walked[-1].superseded_by is not None:
        walked.append(db.get(Memory, walked[-1].superseded_by))

    assert [m.content for m in walked] == [content for content, _ in prices]


def test_forgetting_a_replacement_does_not_leave_a_dangling_link(world):
    """The link is a real foreign key, so deletion nulls it rather than lying."""
    from app.services import memory_control

    db, episode = world
    first = event(db, episode, "The list price for the Pro tier is ₹499.", minutes=0)
    later = event(db, episode, "We dropped the Pro tier price to ₹299.", minutes=90)

    claims.persist(
        db, episode,
        candidate("The list price for the Pro tier is ₹499.",
                  "The list price for the Pro tier is ₹499.", [1]),
        [first],
    )
    original = db.query(Memory).one()
    _index(db, original)
    claims.persist(
        db, episode,
        candidate("The Pro tier price is ₹299 a month.",
                  "We dropped the Pro tier price to ₹299.", [1]),
        [later],
    )
    replacement = db.query(Memory).filter(Memory.status == "active").one()

    memory_control._purge(db, USER, [replacement.id])
    db.expire_all()

    assert db.get(Memory, original.id).superseded_by is None


def test_a_restatement_in_other_words_reinforces_rather_than_duplicates(world):
    db, episode = world
    first = event(db, episode, "The Pro tier price is ₹299 a month.", minutes=0)
    again = event(db, episode, "Pro tier costs ₹299 a month.", minutes=90)

    claims.persist(
        db, episode,
        candidate("The Pro tier price is ₹299 a month.",
                  "The Pro tier price is ₹299 a month.", [1]),
        [first],
    )
    _index(db, db.query(Memory).one())

    outcome = claims.persist(
        db, episode,
        candidate("Pro tier costs ₹299 a month.",
                  "Pro tier costs ₹299 a month.", [1]),
        [again],
    )

    assert outcome == "reinforced"
    assert db.query(Memory).count() == 1
