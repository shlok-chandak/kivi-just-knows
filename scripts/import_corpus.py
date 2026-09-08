#!/usr/bin/env python
"""Import a dictation corpus into the events table.

Accepts the native JSONL format, or any JSONL/CSV whose field names are
remapped with --mapping. Re-running an import updates rows in place rather
than duplicating them.

Examples:
    python -m scripts.import_corpus corpus/fixture.jsonl
    python -m scripts.import_corpus theirs.csv \\
        --mapping '{"timestamp":"occurred_at","transcript":"raw_asr"}'
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import time
import uuid
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert

from app.config import settings
from app.db.session import SessionLocal
from app.models.episode import Episode
from app.models.event import Event
from app.models.job import Job
from app.models.memory import Memory, MemoryEvidence
from app.models.rejected import RejectedCandidate
from app.schemas.event import EventCreate
from app.services import queue
from app.services.apps import normalise_app
from app.services.episodes import ASSIGN_SUBJECT
from app.services.ingest import (
    PreviousDictation,
    build_event_values,
    refuse_if_sensitive,
)
from app.worker.handlers import STAGE_EPISODE_ASSIGN

# Fields the importer understands. Anything else is dropped.
KNOWN_FIELDS = set(EventCreate.model_fields)

# Values a CSV uses to mean "absent".
EMPTY_VALUES = {"", "null", "none", "nan", "na", "-"}

# Fields where an empty string is a value, not a missing one. An empty
# committed_text means the user threw the dictation away, which is the
# strongest signal the junk gate has -- cleaning it to null would erase it.
MEANINGFULLY_EMPTY = {"committed_text"}


def load_records(path: Path, fmt: str) -> Iterator[dict[str, Any]]:
    if fmt == "auto":
        fmt = "csv" if path.suffix.lower() == ".csv" else "jsonl"

    with path.open(newline="", encoding="utf-8") as handle:
        if fmt == "csv":
            yield from csv.DictReader(handle)
        else:
            for line_no, line in enumerate(handle, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise SystemExit(f"{path}:{line_no}: invalid JSON: {exc}") from exc


def load_mapping(raw: str | None) -> dict[str, str]:
    """Read --mapping as inline JSON or a path to a JSON file."""
    if not raw:
        return {}
    candidate = Path(raw)
    text = candidate.read_text(encoding="utf-8") if candidate.is_file() else raw
    mapping = json.loads(text)
    unknown = set(mapping.values()) - KNOWN_FIELDS
    if unknown:
        raise SystemExit(
            f"--mapping targets unknown fields: {sorted(unknown)}\n"
            f"valid targets: {sorted(KNOWN_FIELDS)}"
        )
    return mapping


def normalise(record: dict[str, Any], mapping: dict[str, str]) -> dict[str, Any]:
    """Rename mapped keys, drop unknown ones, and clean CSV-style blanks."""
    renamed = {mapping.get(key, key): value for key, value in record.items()}
    cleaned: dict[str, Any] = {}

    for key, value in renamed.items():
        if key not in KNOWN_FIELDS:
            continue
        if (
            isinstance(value, str)
            and key not in MEANINGFULLY_EMPTY
            and value.strip().lower() in EMPTY_VALUES
        ):
            continue
        cleaned[key] = value

    return cleaned


def synthetic_external_id(payload: EventCreate) -> str:
    """Stable id for corpora that supply none, so re-import stays idempotent."""
    seed = f"{payload.occurred_at.isoformat()}|{payload.raw_asr}"
    digest = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
    return f"auto:{digest}"


def upsert(session, rows: list[dict[str, Any]]) -> None:
    """Insert a batch, updating any row with a matching external_id."""
    statement = insert(Event).values(rows)
    updatable = [
        column.name
        for column in Event.__table__.columns
        if column.name not in {"id", "user_id", "external_id", "created_at"}
    ]
    session.execute(
        statement.on_conflict_do_update(
            index_elements=["user_id", "external_id"],
            set_={name: statement.excluded[name] for name in updatable},
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", type=Path, help="JSONL or CSV corpus file")
    parser.add_argument("--format", choices=["auto", "jsonl", "csv"], default="auto")
    parser.add_argument("--mapping", help="inline JSON or path to a JSON file")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--truncate",
        action="store_true",
        help="delete this user's existing events first",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="abort on the first invalid record instead of skipping it",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="validate only, write nothing"
    )
    args = parser.parse_args(argv)

    if not args.path.is_file():
        raise SystemExit(f"no such file: {args.path}")

    mapping = load_mapping(args.mapping)
    batch_id = uuid.uuid4()
    ingested_at = datetime.now(timezone.utc)
    started = time.perf_counter()

    counts = {"read": 0, "written": 0, "invalid": 0, "refused": 0}
    failures: list[str] = []
    batch: list[dict[str, Any]] = []
    # The last dictation seen in each place, so a re-dictation inside the
    # import is recognised as a retry. Keyed on app and context because the
    # same sentence in two conversations is a repetition, not a failed take.
    last_in_place: dict[tuple[str | None, str | None], PreviousDictation] = {}

    session = SessionLocal()
    try:
        if args.truncate and not args.dry_run:
            # Episodes are derived from events, and jobs refer to groups that
            # are about to disappear. Clearing all three keeps the database
            # consistent instead of leaving orphaned episodes behind.
            deleted = session.execute(
                delete(Event).where(Event.user_id == settings.default_user_id)
            ).rowcount
            session.execute(
                delete(Episode).where(Episode.user_id == settings.default_user_id)
            )
            session.execute(
                delete(Job).where(Job.user_id == settings.default_user_id)
            )
            # Everything derived from those events goes too. A memory whose
            # evidence has been deleted is unfounded, and the spec treats a
            # memory without evidence as a bug rather than a weak belief.
            session.execute(
                delete(MemoryEvidence).where(
                    MemoryEvidence.user_id == settings.default_user_id
                )
            )
            session.execute(
                delete(Memory).where(Memory.user_id == settings.default_user_id)
            )
            # The ignore log too: it records decisions about the events being
            # replaced, so keeping it would accumulate a refusal per re-import
            # and misstate how much was actually declined.
            session.execute(
                delete(RejectedCandidate).where(
                    RejectedCandidate.user_id == settings.default_user_id
                )
            )
            session.commit()
            print(
                f"truncated {deleted} existing events, "
                "plus episodes, memories, jobs and the ignore log"
            )

        for record in load_records(args.path, args.format):
            counts["read"] += 1
            cleaned = normalise(record, mapping)

            try:
                payload = EventCreate(**cleaned)
            except ValidationError as exc:
                counts["invalid"] += 1
                detail = exc.errors()[0]
                label = cleaned.get("external_id") or f"record #{counts['read']}"
                message = f"{label}: {'.'.join(map(str, detail['loc']))}: {detail['msg']}"
                failures.append(message)
                if args.strict:
                    raise SystemExit(f"invalid record: {message}") from exc
                continue

            if not payload.external_id:
                payload.external_id = synthetic_external_id(payload)

            # Refused before anything else: a dictation we are not keeping
            # must not reach storage, and must not become the "previous"
            # dictation that a later retry check compares against.
            category = refuse_if_sensitive(
                session, payload, settings.default_user_id
            )
            if category is not None:
                counts["refused"] += 1
                continue

            place = (normalise_app(payload.app) or None, payload.context_hash)
            values = build_event_values(
                payload,
                user_id=settings.default_user_id,
                source_batch_id=batch_id,
                ingested_at=ingested_at,
                previous=last_in_place.get(place),
            )
            last_in_place[place] = PreviousDictation(
                text=payload.formatted_text, occurred_at=payload.occurred_at
            )
            batch.append(values)
            if len(batch) >= args.batch_size:
                if not args.dry_run:
                    upsert(session, batch)
                counts["written"] += len(batch)
                batch.clear()

        if batch:
            if not args.dry_run:
                upsert(session, batch)
            counts["written"] += len(batch)

        queued = 0
        if not args.dry_run:
            queued = queue.enqueue_many(
                session,
                user_id=settings.default_user_id,
                stage=STAGE_EPISODE_ASSIGN,
                subject_keys=[ASSIGN_SUBJECT],
            )
            session.commit()

        total = session.scalar(
            select(func.count())
            .select_from(Event)
            .where(Event.user_id == settings.default_user_id)
        )
    finally:
        session.close()

    elapsed = time.perf_counter() - started
    verb = "would write" if args.dry_run else "wrote"

    print(f"batch        {batch_id}")
    print(f"read         {counts['read']}")
    print(f"{verb:<12} {counts['written']}")
    print(f"invalid      {counts['invalid']}")
    print(f"refused      {counts['refused']} (sensitive, not stored)")
    print(f"places       {len(last_in_place)} app/context pairs")
    print(f"queued       {queued} assignment job(s)")
    print(f"elapsed      {elapsed:.2f}s ({counts['read'] / max(elapsed, 1e-9):.0f} rec/s)")
    print(f"table total  {total}")

    if failures:
        print("\nfailures:")
        for message in failures[:20]:
            print(f"  {message}")
        if len(failures) > 20:
            print(f"  ... and {len(failures) - 20} more")

    return 1 if counts["invalid"] and args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
