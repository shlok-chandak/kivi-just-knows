"""Score the system on a corpus it has never been tuned against.

The main corpus has been looked at all day: thresholds, prompts and rules
were all chosen while staring at it, so a good score there is partly a
measure of how much was learned from it. This corpus is a different
company, a different domain and a different vocabulary, written before any
of those choices and not read since.

Nothing here may be edited to make a rule pass. A failure is a result.

    python -m scripts.eval_holdout --user-id <uuid>
"""

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path

from app.db.session import SessionLocal
from app.models.event import Event
from app.models.memory import Memory
from app.services import retrieval
from sqlalchemy import select

TRUTH = Path("corpus/holdout_truth.json")
TOP_K = 5

# Asked the way someone would ask, never in the corpus's wording -- matching
# stored phrasing would measure keyword search wearing a coat.
QUESTIONS = {
    "Sundaram Fasteners contract is eighteen rupees a kilo": (
        "what rate did we agree with sundaram", ["eighteen", "18"]),
    "Kumar took over the Coimbatore depot from Ravi": (
        "who runs the coimbatore depot", ["kumar"]),
    "Vendor payments go out on the fifth": (
        "when do we pay vendors", ["fifth", "5th"]),
    "Detention charges start after four hours": (
        "when does detention kick in", ["four hour", "4 hour", "four hours"]),
    "Loader wages rise eight percent from October": (
        "what is happening with loader pay", ["eight percent", "8%", "8 percent"]),
    "No dispatches after eight at night": (
        "how late can we dispatch", ["eight", "8"]),
    "Prefers the LR number in the subject line": (
        "how should i title emails about a consignment", ["lr number", "lr"]),
    "Prefers the daily report as a table, not paragraphs": (
        "how should i lay out the daily report", ["table"]),
}


def _hit(texts: list[str], needles: list[str]) -> int | None:
    for position, text in enumerate(texts, start=1):
        lowered = text.lower()
        if any(needle.lower() in lowered for needle in needles):
            return position
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()

    truth = json.loads(TRUTH.read_text())
    user_id = uuid.UUID(args.user_id)
    now = datetime.now(timezone.utc)

    with SessionLocal() as session:
        print("=== what the corpus expected to be remembered")
        found = ranked_first = 0
        for expected, (question, needles) in QUESTIONS.items():
            hits = retrieval.search_memories(
                session, user_id, question, now=now, limit=TOP_K
            )
            rank = _hit([hit.text for hit in hits], needles)
            found += rank is not None
            ranked_first += rank == 1
            mark = f"rank {rank}" if rank else "MISS "
            print(f"  {mark:7s} {question}")
            if rank is None and hits:
                print(f"           got: {hits[0].text[:66]}")

        total = len(QUESTIONS)
        print(f"\n  recalled {found}/{total}, first {ranked_first}/{total}")

        print("\n=== refusals (ingest, before storage)")
        expected_sensitive = {
            row["external_id"] for row in truth["per_record"]
            if row.get("sensitive_category")
        }
        stored = {
            external_id for external_id in session.scalars(
                select(Event.external_id).where(Event.user_id == user_id)
            )
        }
        refused = expected_sensitive - stored
        print(f"  sensitive refused {len(refused)}/{len(expected_sensitive)}")

        expected_junk = {
            row["external_id"] for row in truth["per_record"]
            if row.get("ignore_reason")
        }
        ignored = {
            external_id for external_id in session.scalars(
                select(Event.external_id).where(
                    Event.user_id == user_id, Event.ingest_status == "ignored"
                )
            )
        }
        caught = expected_junk & ignored
        print(f"  junk ignored      {len(caught)}/{len(expected_junk)}")

        # A refusal that takes real content with it is not a safe default, so
        # the false-positive count matters as much as the recall.
        keepable = {
            row["external_id"] for row in truth["per_record"]
            if row.get("expect") == "stored"
        }
        lost = keepable - stored
        print(f"  wrongly refused   {len(lost)}/{len(keepable)} of keepable records")

        print("\n=== what was built")
        by_status = session.execute(
            select(Memory.status, Memory.type).where(Memory.user_id == user_id)
        ).all()
        counts: dict[str, int] = {}
        for status, kind in by_status:
            counts[f"{status}/{kind}"] = counts.get(f"{status}/{kind}", 0) + 1
        for key in sorted(counts):
            print(f"  {key:24s} {counts[key]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
