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

# The fixture is small and hand-authored, so its size is worth asserting:
# a record silently lost in a rewrite would otherwise go unnoticed. One
# record carries a password and is refused at ingest, so fewer rows land
# than the file holds -- that gap is the point of the sensitive rule.
FIXTURE_RECORDS = 27
# Two records carry sensitive content and are refused at ingest.
FIXTURE_STORED = 25


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


def test_a_window_title_is_hashed_and_not_stored(db):
    """The title often names a contact, which this build does not keep."""
    session, created = db

    title = "Priya Sharma (DM) - Acme - Slack"
    response = post(payload(window_title=title), created)
    row = session.get(Event, uuid.UUID(response.json()["id"]))

    assert row.context_hash is not None
    assert title not in (row.context_hash or "")
    assert "Priya" not in (row.context_hash or "")


def test_the_same_window_groups_the_same_way(db):
    """Grouping only ever asks whether two events share a context."""
    session, created = db

    first = post(payload(window_title="#pricing (Acme) - Slack"), created)
    second = post(
        payload(
            window_title="#PRICING (Acme) - Slack",
            occurred_at="2026-09-06T17:09:11+05:30",
        ),
        created,
    )

    a = session.get(Event, uuid.UUID(first.json()["id"]))
    b = session.get(Event, uuid.UUID(second.json()["id"]))
    assert a.context_hash == b.context_hash


def test_a_client_may_supply_its_own_hash(db):
    """A client that would rather not send the title hashes it on device."""
    session, created = db

    response = post(payload(context_hash="deadbeefdeadbeef"), created)
    row = session.get(Event, uuid.UUID(response.json()["id"]))
    assert row.context_hash == "deadbeefdeadbeef"


def test_a_discarded_dictation_is_stored_but_flagged(db):
    """Content is kept either way -- only the embedding is withheld."""
    session, created = db

    response = post(payload(committed_text=""), created)
    row = session.get(Event, uuid.UUID(response.json()["id"]))

    assert row.ingest_status == "ignored"
    assert row.ignore_reason == "discarded_by_user"
    assert row.formatted_text == "I think we should go with 299."


def test_the_app_name_is_normalised_on_the_way_in(db):
    """One normalisation, at write time, so no SQL has to reimplement it."""
    session, created = db

    response = post(payload(app="1 Password"), created)
    row = session.get(Event, uuid.UUID(response.json()["id"]))
    assert row.app == "1password"


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

    assert first == FIXTURE_STORED
    assert count() == first


def test_import_stores_every_record_with_its_content():
    """Ingestion is unconditional: no app is filtered at the write path.

    Some records are flagged as junk and will not be embedded, but every one
    keeps its text, so the user can always find what they said.
    """
    from scripts.import_corpus import main

    main(["corpus/fixture.jsonl", "--truncate"])

    session = SessionLocal()
    try:
        rows = session.scalars(
            select(Event).where(Event.user_id == settings.default_user_id)
        ).all()
        assert len(rows) == FIXTURE_STORED
        assert all(row.raw_asr for row in rows)
        assert all(row.ingest_status in ("pending", "ignored") for row in rows)
    finally:
        session.close()


def test_the_importer_applies_the_junk_gate():
    """The fixture carries one of each rejection, so all five stay covered."""
    from scripts.import_corpus import main

    main(["corpus/fixture.jsonl", "--truncate"])

    session = SessionLocal()
    try:
        reasons = set(
            session.scalars(
                select(Event.ignore_reason).where(
                    Event.user_id == settings.default_user_id,
                    Event.ignore_reason.is_not(None),
                )
            )
        )
        assert "discarded_by_user" in reasons
        assert "low_asr_confidence" in reasons
        assert "superseded_by_retry" in reasons
    finally:
        session.close()
