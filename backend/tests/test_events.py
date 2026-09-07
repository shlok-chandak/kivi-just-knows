"""Ingest tests: denylist enforcement, validation, importer idempotency."""

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select

from app.config import settings
from app.db.session import SessionLocal
from app.main import app
from app.models.event import Event
from app.services.denylist import is_denylisted, normalise_app

client = TestClient(app)

SECRET = "correct-horse-battery-staple"


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


# --- denylist ---------------------------------------------------------------


@pytest.mark.parametrize(
    "app_name",
    ["1password", "1Password", "  BANKING  ", "password-manager", "Password Manager"],
)
def test_denylist_matches_across_spellings(app_name):
    assert is_denylisted(app_name)


@pytest.mark.parametrize("app_name", ["slack", "gmail", "notion", None, "", "lastpass"])
def test_denylist_allows_other_apps(app_name):
    assert not is_denylisted(app_name)


def test_normalise_app_handles_none():
    assert normalise_app(None) == ""


def test_denylisted_event_stores_no_content(db):
    session, created = db

    response = post(payload(app="1Password", raw_asr=SECRET, formatted_text=SECRET), created)
    assert response.status_code == 201

    body = response.json()
    assert body["ingest_status"] == "ignored"
    assert body["ignore_reason"] == "denylisted_app:1password"
    assert body["raw_asr"] is None

    # The response hiding the content is not the same claim as the database
    # not holding it. Assert the stronger one.
    row = session.get(Event, uuid.UUID(body["id"]))
    session.refresh(row)
    assert row.raw_asr is None
    assert row.formatted_text is None
    assert row.committed_text is None

    leaked = session.scalar(
        select(func.count())
        .select_from(Event)
        .where(Event.raw_asr.ilike(f"%{SECRET}%"))
    )
    assert leaked == 0


def test_denylisted_event_keeps_auditable_metadata(db):
    """The ignore must be provable: the row exists, minus the content."""
    session, created = db

    response = post(payload(app="banking", duration_ms=3200), created)
    row = session.get(Event, uuid.UUID(response.json()["id"]))

    assert row.app == "banking"
    assert row.duration_ms == 3200
    assert row.occurred_at is not None
    assert row.ingest_status == "ignored"


def test_allowed_event_is_stored_and_pending(db):
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


def test_import_stores_no_content_for_denylisted_records():
    from scripts.import_corpus import main

    main(["corpus/fixture.jsonl", "--truncate"])

    session = SessionLocal()
    try:
        ignored = session.scalars(
            select(Event).where(Event.ingest_status == "ignored")
        ).all()
        assert {row.external_id for row in ignored} == {"f_005", "f_006"}
        assert all(row.raw_asr is None for row in ignored)
        assert all(row.app is not None for row in ignored)
    finally:
        session.close()
