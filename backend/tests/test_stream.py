"""Watching work happen, rather than reading about it afterwards.

The claim these tests defend is that the live view and the stored trace
cannot disagree, because one call feeds both. So they assert on the pairing:
every stage that reached the database also reached the reader, in order.
"""

import json
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.trace import Trace, TraceStep
from app.services import asking, tracing

client = TestClient(app)
USER = settings.default_user_id

QUESTION = "what is the price"


def frames(body: str) -> list[tuple[str, dict]]:
    """Parse an SSE body into (event, data) pairs, ignoring heartbeats."""
    out: list[tuple[str, dict]] = []
    kind = None
    for line in body.splitlines():
        if line.startswith("event: "):
            kind = line[7:]
        elif line.startswith("data: ") and kind:
            out.append((kind, json.loads(line[6:])))
    return out


# --- the listener itself ----------------------------------------------------


def test_a_recorder_with_no_listener_behaves_exactly_as_before():
    """The hook is optional, and absent is the normal case."""
    with SessionLocal() as session:
        recorder = tracing.start_query_trace(session, user_id=USER, request="hello")
        recorder.step("parse", decision="recall: hello")
        recorder.close("answered")
        assert recorder.trace.outcome == "answered"
        session.rollback()


def test_every_step_reaches_the_listener_in_order():
    seen: list[str] = []
    with SessionLocal() as session:
        recorder = tracing.start_query_trace(
            session, user_id=USER, request="hello",
            on_step=lambda step: seen.append(step.stage),
        )
        recorder.step("parse", decision="a")
        recorder.step("plan", decision="b")
        recorder.step("recall", decision="c")
        recorder.close("answered")
        session.rollback()

    assert seen == ["parse", "plan", "recall"]


def test_a_listener_that_throws_does_not_take_the_run_down():
    """Someone closing a tab must not fail the question they asked."""
    def explode(step):
        raise RuntimeError("the reader went away")

    with SessionLocal() as session:
        recorder = tracing.start_query_trace(
            session, user_id=USER, request="hello", on_step=explode
        )
        step = recorder.step("parse", decision="still recorded")
        recorder.close("answered")

        assert step.id is not None
        assert recorder.trace.outcome == "answered"
        session.rollback()


def test_the_listener_only_sees_steps_the_database_accepted():
    """Flushed before announcing, so a watcher never sees a phantom stage."""
    ids: list[uuid.UUID] = []
    with SessionLocal() as session:
        recorder = tracing.start_query_trace(
            session, user_id=USER, request="hello",
            on_step=lambda step: ids.append(step.id),
        )
        recorder.step("parse", decision="a")
        recorder.close("answered")
        session.rollback()

    assert ids and all(value is not None for value in ids)


# --- the endpoint -----------------------------------------------------------


@pytest.fixture
def answered(monkeypatch):
    """A question that runs without a provider, so the shape can be asserted."""
    def fake_run(session, user_id, request, *, on_step=None, **kwargs):
        recorder = tracing.start_query_trace(
            session, user_id=user_id, request=request, on_step=on_step
        )
        recorder.step("parse", decision=f"recall: {request}")
        recorder.step("plan", decision="recall")
        recorder.step("recall", decision="answered from 2 sources")
        recorder.close("answered", final_output="Two hundred and ninety nine.")
        return asking.Outcome(
            request=request,
            understood_as={"intent": "recall"},
            plan={"tools": ["recall"]},
            steps=[{"tool": "recall", "result": {"answer": "…"}}],
            trace_id=recorder.id,
            outcome="answered",
        )

    monkeypatch.setattr(asking, "run", fake_run)
    yield
    # Only what this test wrote. The test database is shared between tests,
    # so a blanket delete here would quietly break whatever runs next.
    with SessionLocal() as session:
        mine = select(Trace.id).where(Trace.input == QUESTION)
        session.execute(delete(TraceStep).where(TraceStep.trace_id.in_(mine)))
        session.execute(delete(Trace).where(Trace.input == QUESTION))
        session.commit()


def test_the_stages_arrive_before_the_answer(answered):
    """The whole point: stages as they finish, not all at the end."""
    with client.stream("GET", "/stream/ask?request=what+is+the+price") as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        received = frames("".join(response.iter_text()))

    kinds = [kind for kind, _ in received]
    assert kinds[0] == "hello"
    assert kinds[-1] == "done"
    assert kinds.index("step") < kinds.index("answer")
    assert kinds.count("step") == 3


def test_what_was_streamed_is_what_was_stored(answered):
    """One call feeds the live view and the trace, so they cannot diverge."""
    with client.stream("GET", "/stream/ask?request=what+is+the+price") as response:
        received = frames("".join(response.iter_text()))

    streamed = [data for kind, data in received if kind == "step"]
    trace_id = uuid.UUID(dict(received)["done"]["trace_id"])

    with SessionLocal() as session:
        stored = list(
            session.scalars(
                select(TraceStep)
                .where(TraceStep.trace_id == trace_id)
                .order_by(TraceStep.seq)
            )
        )

    assert [step["stage"] for step in streamed] == [step.stage for step in stored]
    assert [step["decision"] for step in streamed] == [
        step.decision for step in stored
    ]


def test_an_empty_question_is_refused_before_any_work(answered):
    assert client.get("/stream/ask?request=").status_code == 422
