"""Give already-stored records the vectors they were created without.

Events, summaries and memories predate the embeddings table, so the corpus
run produced memory that is correct and unfindable. Re-running the pipeline
would rebuild it, but consolidation costs model calls and the beliefs it
would produce are the ones already sitting in the database. Only the local,
free half is missing, so only that half is redone here.

The texts are the ones the live path embeds -- an event's canonical text, an
episode's summary without its title, a memory's content without its subject.
That is not tidiness: a query is compared against whatever was indexed, so
backfilling different text than ingest writes would make results depend on
which run happened to store a row.

Idempotent, because store() replaces rather than appends. Running it twice
costs time and changes nothing.

    python -m scripts.backfill_embeddings --user-id <uuid>
"""

import argparse
import uuid

from sqlalchemy import select

from app.config import settings
from app.db.session import SessionLocal
from app.models.embedding import Embedding
from app.models.episode import Episode
from app.models.event import Event
from app.models.memory import Memory
from app.services import embed

# Vectors are held in memory between encoding and writing, so the batch size
# bounds peak memory rather than any provider limit.
BATCH = 128


def _already_embedded(session, user_id: uuid.UUID, object_type: str) -> set[uuid.UUID]:
    """Objects of this type that already have a vector for the current model."""
    return set(
        session.scalars(
            select(Embedding.object_id).where(
                Embedding.user_id == user_id,
                Embedding.object_type == object_type,
                Embedding.model == settings.embedding_model,
            )
        )
    )


def _pending(session, user_id: uuid.UUID) -> list[tuple[str, uuid.UUID, str]]:
    """Every indexable record with no vector yet, as store() expects them."""
    items: list[tuple[str, uuid.UUID, str]] = []

    # Junk is deliberately excluded. A mic test does not need to be findable,
    # and indexing it means it competes with dictations that do.
    done = _already_embedded(session, user_id, "event")
    for event in session.scalars(
        select(Event).where(
            Event.user_id == user_id,
            Event.ingest_status != "ignored",
        )
    ):
        if event.id not in done:
            items.append(("event", event.id, event.canonical_text or ""))

    done = _already_embedded(session, user_id, "episode")
    for episode in session.scalars(
        select(Episode).where(
            Episode.user_id == user_id,
            Episode.summary.is_not(None),
        )
    ):
        if episode.id not in done:
            items.append(("episode", episode.id, episode.summary or ""))

    done = _already_embedded(session, user_id, "memory")
    for memory in session.scalars(select(Memory).where(Memory.user_id == user_id)):
        if memory.id not in done:
            items.append(("memory", memory.id, memory.content))

    return items


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what is missing without loading the model",
    )
    args = parser.parse_args()

    user_id = uuid.UUID(args.user_id)

    with SessionLocal() as session:
        items = _pending(session, user_id)

        counts: dict[str, int] = {}
        for object_type, _, _ in items:
            counts[object_type] = counts.get(object_type, 0) + 1
        print(f"missing vectors: {counts or 'none'}")

        # Empty text cannot be embedded, and a record that has none is a
        # separate problem from a record that was never indexed. Say so here
        # rather than letting store() silently skip it.
        blank = sum(1 for _, _, text in items if not text.strip())
        if blank:
            print(f"warning: {blank} records have no text and will be skipped")

        if args.dry_run or not items:
            return 0

        written = 0
        for start in range(0, len(items), BATCH):
            batch = items[start : start + BATCH]
            written += embed.store(session, user_id, batch)
            session.commit()
            print(f"  {written}/{len(items)}")

        print(f"wrote {written} vectors with {settings.embedding_model}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
