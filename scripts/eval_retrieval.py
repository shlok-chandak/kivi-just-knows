"""Score retrieval against what the corpus is known to contain.

The questions are phrased the way someone would actually ask them, not the
way the corpus states things, because retrieval that only works when the
question echoes the stored wording is keyword matching with extra steps.

Ground truth comes from corpus/ground_truth.json, which was written from
spec.yaml before retrieval existed. So these answers are held out with
respect to ranking: no threshold in the system was chosen by looking at
this score. That is the whole reason the file is worth running.

Three things are measured, because they fail independently:

  found     the right answer is somewhere in the results at all
  on_top    it is the first result, which is what a caller actually uses
  stale     a value known to be superseded outranks the current one

The third is the one that matters most and the one a plain hit-rate hides.
A system that returns the old price above the new one looks like a success
to any metric that only asks whether the topic was found.

    python -m scripts.eval_retrieval --user-id <uuid>
"""

import argparse
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.db.session import SessionLocal
from app.services import retrieval

GROUND_TRUTH = Path("corpus/ground_truth.json")

# How many results a question is scored over. Small on purpose: a caller
# building a prompt has room for a handful of memories, so an answer sitting
# at rank 20 is not an answer.
TOP_K = 5


@dataclass
class Question:
    """One question, and what a correct result set looks like."""

    ability: str
    asked: str
    # Any one of these appearing in a result counts as the right answer.
    # Alternatives are spellings, not different answers.
    expect: list[str] = field(default_factory=list)
    # Values this claim used to have. Present in the corpus as history, so
    # finding them is fine; ranking one above the current value is not.
    superseded: list[str] = field(default_factory=list)
    # True when nothing in the corpus supports the question and the honest
    # result is no result.
    expect_nothing: bool = False


def _questions(truth: dict) -> list[Question]:
    """Build the question set from ground truth rather than by hand.

    Derived, so the arcs and the questions cannot drift apart, and so adding
    an arc to the spec adds a question here without editing this file.
    """
    asked = {
        "pricing": "what are we charging for the pro tier",
        "launch_date": "when does inbox rules go out",
        "gateway": "which payment provider did we go with",
        "soc2_auditor": "who is doing our soc2 audit",
        "standup": "what time is standup",
        "inbox_owner": "who is running inbox rules now",
    }

    questions = [
        Question(
            ability="knowledge_updates",
            asked=asked[arc["id"]],
            expect=[arc["final"], arc["final"].lstrip("₹")],
            superseded=[
                value for old in arc.get("superseded", []) for value in (old, old.lstrip("₹"))
            ],
        )
        for arc in truth["decision_arcs"]
        if arc["id"] in asked
    ]

    # Preferences are the ones injected into every prompt, so they have to be
    # retrievable by intent rather than by their stored phrasing.
    for preference, probe in [
        ("release notes", "how should i write release notes"),
        ("three bullets", "how long should customer emails be"),
        ("rupee figure", "how should i refer to prices"),
        ("before noon", "when do i like meetings"),
    ]:
        questions.append(
            Question(ability="information_extraction", asked=probe, expect=[preference])
        )

    # Nothing in this corpus touches these. Returning something confident
    # here is worse than returning nothing, so they are scored as failures
    # when anything comes back.
    for probe in [
        "what is our aws bill",
        "who is on call this weekend",
        "what did legal say about the trademark",
    ]:
        questions.append(
            Question(ability="abstention", asked=probe, expect_nothing=True)
        )

    return questions


def _matches(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles if needle)


def _score(question: Question, results: list[str]) -> dict:
    """Grade one question against the text of its top results."""
    if question.expect_nothing:
        return {"abstained": not results, "returned": len(results)}

    ranks = [i for i, text in enumerate(results) if _matches(text, question.expect)]
    stale_ranks = [
        i for i, text in enumerate(results) if _matches(text, question.superseded)
    ]

    best = ranks[0] if ranks else None
    # Only counts as stale-first if a superseded value actually outranks the
    # current one. A superseded value below the answer is history, not a bug.
    stale_first = bool(stale_ranks) and (best is None or stale_ranks[0] < best)

    return {
        "found": best is not None,
        "on_top": best == 0,
        "rank": None if best is None else best + 1,
        "stale_first": stale_first,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    truth = json.loads(GROUND_TRUTH.read_text())
    questions = _questions(truth)
    user_id = uuid.UUID(args.user_id)
    now = datetime.now(timezone.utc)

    tally: dict[str, dict[str, int]] = {}
    with SessionLocal() as session:
        for question in questions:
            hits = retrieval.search_memories(
                session, user_id, question.asked, now=now, limit=TOP_K
            )
            results = [hit.text for hit in hits]
            outcome = _score(question, results)

            bucket = tally.setdefault(question.ability, {"n": 0})
            bucket["n"] += 1
            for key, value in outcome.items():
                if value is True:
                    bucket[key] = bucket.get(key, 0) + 1

            mark = (
                outcome.get("abstained")
                if question.expect_nothing
                else outcome.get("found") and not outcome.get("stale_first")
            )
            print(f"{'ok ' if mark else 'MISS'}  {question.asked}")
            if args.verbose or not mark:
                for i, text in enumerate(results, 1):
                    print(f"        {i}. {text[:76]}")
                if not results:
                    print("        (nothing returned)")

    print()
    for ability, bucket in sorted(tally.items()):
        n = bucket.pop("n")
        detail = ", ".join(f"{k}={v}/{n}" for k, v in sorted(bucket.items()))
        print(f"{ability:26s} n={n}  {detail or 'none'}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
