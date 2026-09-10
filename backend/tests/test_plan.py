"""Planning: which tools run for a parsed request.

Rules, so every case here is deterministic and none of them calls a model.
"""

from app.schemas.query import QuerySpec
from app.services.plan import MAX_STEPS, plan


def spec(**kwargs) -> QuerySpec:
    return QuerySpec(intent=kwargs.pop("intent", "recall"),
                     topic=kwargs.pop("topic", "pricing"), **kwargs)


def test_a_question_is_answered_from_history():
    assert plan(spec()).tools == ["recall"]


def test_finding_a_dictation_is_one_step():
    assert plan(spec(intent="find_dictation")).tools == ["find_dictation"]


def test_finding_then_polishing_is_the_briefs_chained_example():
    """"Find the 5 PM dictation and polish it" is two tools, not two requests."""
    chained = plan(spec(intent="find_dictation", style_hint="for the meeting"))
    assert chained.tools == ["find_dictation", "restyle"]
    assert chained.steps[1].takes_input_from == "find_dictation"


def test_a_plain_find_does_not_restyle():
    assert plan(spec(intent="find_dictation", style_hint=None)).tools == [
        "find_dictation"
    ]


def test_drafting_retrieves_before_it_writes():
    """Composing without retrieving first is writing fiction."""
    drafted = plan(spec(intent="draft", artifact="message"))
    assert drafted.tools == ["recall", "draft"]
    assert drafted.steps[1].takes_input_from == "recall"


def test_restyling_supplied_text_needs_no_retrieval():
    assert plan(spec(intent="restyle")).tools == ["restyle"]


def test_memory_control_is_a_single_step_either_way():
    assert plan(spec(intent="memory_control")).tools == ["memory_control"]
    assert plan(spec(intent="memory_control", forget=True)).tools == [
        "memory_control"
    ]


def test_forgetting_is_stated_in_the_rationale():
    """The trace has to distinguish showing from deleting."""
    assert "delete" in plan(spec(intent="memory_control", forget=True)).rationale
    assert "show" in plan(spec(intent="memory_control")).rationale


def test_no_plan_exceeds_the_cap():
    for intent in ("recall", "find_dictation", "restyle", "draft", "memory_control"):
        made = plan(spec(intent=intent, style_hint="casual", forget=True))
        assert len(made.steps) <= MAX_STEPS


# --- finder: what selects the candidates ------------------------------------


def test_a_topic_search_is_not_capped_to_the_newest_dictations():
    """The candidate set must come from the topic when nothing else narrows.

    finder selected the newest N by time and then let the topic reorder only
    those, so a dictation older than N could never be found however well it
    matched. The topic search ran, found it, and its score was discarded.
    """
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import delete

    from app.db.session import SessionLocal
    from app.models.event import Event
    from app.services import finder
    from tests.conftest import TEST_USER

    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    session = SessionLocal()
    made: list[_uuid.UUID] = []

    def add(text: str, days_ago: int) -> Event:
        row = Event(
            id=_uuid.uuid4(),
            user_id=TEST_USER,
            occurred_at=now - timedelta(days=days_ago),
            ingested_at=now - timedelta(days=days_ago),
            app="slack",
            raw_asr=text.lower(),
            formatted_text=text,
            committed_text=text,
            ingest_status="pending",
        )
        session.add(row)
        session.flush()
        made.append(row.id)
        return row

    try:
        # The one that matters, buried behind more recent, unrelated chatter.
        target = add("The zephyr reconciliation settlement is on Tuesday.", 60)
        for day in range(1, 15):
            add(f"Running {day} minutes late.", day)
        session.commit()

        result = finder.find(
            session, TEST_USER, now=now, topic="zephyr reconciliation settlement"
        )

        assert target.id in {item.event.id for item in result.found}
    finally:
        session.execute(delete(Event).where(Event.id.in_(made)))
        session.commit()
        session.close()
