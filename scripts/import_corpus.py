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
from app.models.event import Event
from app.schemas.event import EventCreate
from app.services.ingest import build_event_values

# Fields the importer understands. Anything else is dropped.
KNOWN_FIELDS = set(EventCreate.model_fields)

# Values a CSV uses to mean "absent".
EMPTY_VALUES = {"", "null", "none", "nan", "na", "-"}


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
        if isinstance(value, str) and value.strip().lower() in EMPTY_VALUES:
            continue
        cleaned[key] = value

    # CSV has no lists: accept "Aditya; Priya" or "Aditya, Priya".
    recipients = cleaned.get("recipients")
    if isinstance(recipients, str):
        separator = ";" if ";" in recipients else ","
        cleaned["recipients"] = [
            part.strip() for part in recipients.split(separator) if part.strip()
        ]

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

    counts = {"read": 0, "written": 0, "ignored": 0, "invalid": 0}
    failures: list[str] = []
    batch: list[dict[str, Any]] = []

    session = SessionLocal()
    try:
        if args.truncate and not args.dry_run:
            deleted = session.execute(
                delete(Event).where(Event.user_id == settings.default_user_id)
            ).rowcount
            session.commit()
            print(f"truncated {deleted} existing events")

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

            values = build_event_values(
                payload,
                user_id=settings.default_user_id,
                source_batch_id=batch_id,
                ingested_at=ingested_at,
            )
            if values["ingest_status"] == "ignored":
                counts["ignored"] += 1

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

        if not args.dry_run:
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
    print(f"  ignored    {counts['ignored']} (denylisted app, content not stored)")
    print(f"invalid      {counts['invalid']}")
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
