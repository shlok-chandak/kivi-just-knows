"""Ingest tests: validation, storage, and importer idempotency."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.event import Event

client = TestClient(app)


def payload(**overrides):
    base = {
        "occurred_at": "2026-09-06T17:04:11+05:30",
        "raw_asr": "so i think we should go with two ninety nine",
        "formatted_text": "I think we should go with 299.",
        "app": "slack",
    }
    base.update(overrides)
    return base


@pytest.fixture
def db():
    session = SessionLocal()
    created: list[uuid.UUID] = []
    yield session, created
    if created:
        session.execute(delete(Event).where(Event.id.in_(created)))
        session.commit()
    session.close()


def post(body, created):
    response = client.post("/events", json=body)
    if response.status_code == 201:
        created.append(uuid.UUID(response.json()["id"]))
    return response


# --- storage ----------------------------------------------------------------


def test_every_event_is_stored_and_pending(db):
    session, created = db

    response = post(payload(), created)
    assert response.status_code == 201

    row = session.get(Event, uuid.UUID(response.json()["id"]))
    assert row.ingest_status == "pending"
    assert row.formatted_text == "I think we should go with 299."


# --- validation -------------------------------------------------------------


def test_naive_timestamp_is_rejected():
    response = client.post("/events", json=payload(occurred_at="2026-09-06T17:04:11"))
    assert response.status_code == 422
    assert "timezone" in response.text


def test_timezone_offset_is_preserved_as_the_same_instant(db):
    _, created = db

    response = post(payload(occurred_at="2026-09-06T17:04:11+05:30"), created)
    # 17:04:11+05:30 is 11:34:11Z. Same instant, rendered in UTC.
    assert response.json()["occurred_at"].startswith("2026-09-06T11:34:11")


@pytest.mark.parametrize(
    "body",
    [
        payload(raw_asr="   "),
        payload(formatted_text=""),
        payload(asr_confidence=1.4),
        payload(duration_ms=-1),
    ],
)
def test_invalid_payloads_are_rejected(body):
    assert client.post("/events", json=body).status_code == 422


def test_unknown_fields_are_ignored(db):
    _, created = db

    response = post(
        payload(metadata={"device": "macbook", "locale": "en-IN"}, nonsense="x"),
        created,
    )
    assert response.status_code == 201


def test_empty_recipients_are_dropped(db):
    session, created = db

    response = post(payload(recipients=["Aditya", "", "   "]), created)
    row = session.get(Event, uuid.UUID(response.json()["id"]))
    assert row.recipients == ["Aditya"]


# --- reads ------------------------------------------------------------------


def test_get_event_round_trips(db):
    _, created = db

    created_id = post(payload(), created).json()["id"]
    assert client.get(f"/events/{created_id}").json()["id"] == created_id


def test_get_missing_event_is_404():
    assert client.get(f"/events/{uuid.uuid4()}").status_code == 404


# --- importer ---------------------------------------------------------------


def test_import_is_idempotent():
    """Re-importing the fixture must update rows, not duplicate them."""
    from scripts.import_corpus import main

    def count():
        session = SessionLocal()
        try:
            return session.scalar(
                select(func.count())
                .select_from(Event)
                .where(Event.user_id == settings.default_user_id)
            )
        finally:
            session.close()

    main(["corpus/fixture.jsonl", "--truncate"])
    first = count()
    main(["corpus/fixture.jsonl"])

    assert first == 15
    assert count() == first


def test_import_stores_every_record_with_its_content():
    """Ingestion is unconditional: no app is filtered at the write path.

    Sensitive content is handled downstream, on the memory candidate, so the
    dictation itself stays findable.
    """
    from scripts.import_corpus import main

    main(["corpus/fixture.jsonl", "--truncate"])

    session = SessionLocal()
    try:
        rows = session.scalars(
            select(Event).where(Event.user_id == settings.default_user_id)
        ).all()
        assert len(rows) == 15
        assert all(row.ingest_status == "pending" for row in rows)
        assert all(row.raw_asr for row in rows)
    finally:
        session.close()
