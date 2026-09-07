"""The summarisation gate: which episodes reach a model, and which do not.

The model itself is stubbed. What is under test is the routing and the
accounting, not the provider's prose.
"""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete, select

from app.config import settings
from app.db.session import SessionLocal
from app.llm.client import Completion, Usage
from app.models.episode import Episode, EpisodeEvent
from app.models.event import Event
from app.models.job import Job
from app.models.trace import Trace, TraceStep
from app.schemas.summary import EpisodeSummaryOut
from app.services import summarise as summarise_module
from app.services.episodes import rebuild_group
from app.services.summarise import VERBATIM_PAIR_MAX_CHARS, summarise_episode
from app.services.tracing import start_ingest_trace

USER = settings.default_user_id
BASE = datetime(2026, 9, 4, 11, 0, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 8, tzinfo=timezone.utc)
THREAD = "summarise-test"
GROUP = f"t:slack:{THREAD}"

STUB_SUMMARY = EpisodeSummaryOut(
    title="Stubbed title",
    summary="A stubbed summary of the sitting.",
    topic_tags=["stub"],
)
STUB_USAGE = Usage(
    model="stub-model",
    input_tokens=100,
    output_tokens=20,
    latency_ms=42,
    cost_usd=0.000123,
)


class StubClient:
    """Stands in for the provider, counting how often it was asked."""

    def __init__(self) -> None:
        self.calls = 0

    def structured(self, **kwargs):
        self.calls += 1
        return Completion(value=STUB_SUMMARY, usage=STUB_USAGE)


@pytest.fixture
def stub(monkeypatch) -> StubClient:
    client = StubClient()
    monkeypatch.setattr(summarise_module, "get_client", lambda: client)
    return client


@pytest.fixture
def session():
    db = SessionLocal()
    _clear(db)
    yield db
    _clear(db)
    db.close()


def _clear(db) -> None:
    db.execute(delete(Job).where(Job.user_id == USER))
    db.execute(delete(Trace).where(Trace.user_id == USER))
    db.execute(delete(Episode).where(Episode.group_key == GROUP))
    db.execute(delete(Event).where(Event.thread_id == THREAD))
    db.commit()


def add_events(db, texts: list[str], gap_minutes: int = 2) -> None:
    for index, text in enumerate(texts):
        db.add(
            Event(
                user_id=USER,
                occurred_at=BASE + timedelta(minutes=index * gap_minutes),
                ingested_at=BASE,
                app="slack",
                thread_id=THREAD,
                raw_asr=text.lower(),
                formatted_text=text,
                ingest_status="pending",
            )
        )
    db.commit()


def build_episode(db) -> uuid.UUID:
    rebuild_group(db, USER, GROUP, now=NOW)
    db.commit()
    return db.scalars(
        select(Episode.id).where(Episode.group_key == GROUP)
    ).one()


# --- the gate ---------------------------------------------------------------


def test_a_single_dictation_is_its_own_summary(session, stub):
    add_events(session, ["We settled on 299 for the Pro tier."])
    episode_id = build_episode(session)

    summarise_episode(session, episode_id)
    session.commit()

    episode = session.get(Episode, episode_id)
    assert episode.summary_status == "verbatim"
    assert episode.summary == "We settled on 299 for the Pro tier."
    assert stub.calls == 0


def test_two_short_dictations_are_concatenated(session, stub):
    add_events(session, ["Pricing call at four.", "Bring the churn numbers."])
    episode_id = build_episode(session)

    summarise_episode(session, episode_id)
    session.commit()

    episode = session.get(Episode, episode_id)
    assert episode.summary_status == "verbatim"
    assert "Pricing call" in episode.summary
    assert "churn numbers" in episode.summary
    assert stub.calls == 0


def test_two_long_dictations_reach_the_model(session, stub):
    """The pair shortcut is a length rule, not just a count rule."""
    long_text = "x" * VERBATIM_PAIR_MAX_CHARS
    add_events(session, [long_text, long_text])
    episode_id = build_episode(session)

    summarise_episode(session, episode_id)
    session.commit()

    assert session.get(Episode, episode_id).summary_status == "generated"
    assert stub.calls == 1


def test_three_dictations_reach_the_model(session, stub):
    add_events(session, ["First point.", "Second point.", "Third point."])
    episode_id = build_episode(session)

    summarise_episode(session, episode_id)
    session.commit()

    episode = session.get(Episode, episode_id)
    assert episode.summary_status == "generated"
    assert episode.title == "Stubbed title"
    assert episode.topic_tags == ["stub"]
    assert stub.calls == 1


def test_an_episode_without_text_is_skipped(session, stub):
    """Nothing to summarise, so nothing is spent trying."""
    session.add(
        Event(
            user_id=USER,
            occurred_at=BASE,
            ingested_at=BASE,
            app="slack",
            thread_id=THREAD,
            raw_asr=None,
            formatted_text=None,
            ingest_status="ignored",
        )
    )
    session.commit()
    episode_id = build_episode(session)

    summarise_episode(session, episode_id)
    session.commit()

    assert session.get(Episode, episode_id).summary_status == "skipped"
    assert stub.calls == 0


def test_a_missing_episode_is_an_error(session, stub):
    with pytest.raises(ValueError):
        summarise_episode(session, uuid.uuid4())


# --- accounting -------------------------------------------------------------


def test_a_model_call_is_recorded_with_its_cost(session, stub):
    add_events(session, ["First point.", "Second point.", "Third point."])
    episode_id = build_episode(session)

    with start_ingest_trace(
        session, user_id=USER, subject_key=str(episode_id)
    ) as recorder:
        summarise_episode(session, episode_id, recorder=recorder)
        recorder.close("answered")
    session.commit()

    step = session.scalars(
        select(TraceStep).where(TraceStep.stage == "episode_summarise")
    ).one()
    assert step.model == "stub-model"
    assert step.input_tokens == 100
    assert step.output_tokens == 20
    assert step.cost_usd == pytest.approx(0.000123, rel=1e-3)
    assert step.decision == "generated"

    trace = session.get(Trace, step.trace_id)
    assert trace.total_input_tokens == 100
    assert trace.total_cost_usd == pytest.approx(0.000123, rel=1e-3)


def test_a_skipped_call_is_still_recorded_but_costs_nothing(session, stub):
    """A stage that avoided a model must still explain itself."""
    add_events(session, ["Only one dictation here."])
    episode_id = build_episode(session)

    with start_ingest_trace(
        session, user_id=USER, subject_key=str(episode_id)
    ) as recorder:
        summarise_episode(session, episode_id, recorder=recorder)
        recorder.close("answered")
    session.commit()

    step = session.scalars(
        select(TraceStep).where(TraceStep.stage == "episode_summarise")
    ).one()
    assert step.decision == "verbatim"
    assert step.model is None
    assert step.cost_usd is None
    assert "single dictation" in step.rationale

    assert session.get(Trace, step.trace_id).total_cost_usd == 0.0


# --- rebuild ----------------------------------------------------------------


def test_a_rebuild_keeps_an_existing_summary(session, stub):
    """Resummarising an unchanged episode would spend money for nothing."""
    add_events(session, ["First point.", "Second point.", "Third point."])
    episode_id = build_episode(session)

    summarise_episode(session, episode_id)
    session.commit()
    assert stub.calls == 1

    result = rebuild_group(session, USER, GROUP, now=NOW)
    session.commit()

    episode = session.get(Episode, episode_id)
    assert episode.summary_status == "generated"
    assert episode.summary == STUB_SUMMARY.summary
    assert result["needs_summary"] == []


def test_an_open_episode_is_not_queued_for_summary(session, stub):
    """An open sitting is still growing; summarising it now wastes the call."""
    add_events(session, ["Just said this."])
    just_now = BASE + timedelta(minutes=1)

    result = rebuild_group(session, USER, GROUP, now=just_now)
    session.commit()

    assert result["open"] == 1
    assert result["needs_summary"] == []
