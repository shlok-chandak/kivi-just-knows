"""Run every tool against the corpus none of them was built on.

Profile was written and tested against the main corpus, using requests invented
by whoever was building it. That measures whether the tools work on
phrasings their author imagined -- which is the same mistake the sensitive
word list made, and it scored 100% in-sample and 10% held out.

This is a different company, a different trade and a different vocabulary:
freight and warehousing in Chennai rather than a software team. Nothing in
the parser, planner or finder was tuned against it.

Failures here are the point. Nothing in this file may be edited to make one
pass.

    python -m scripts.eval_tools_holdout --user-id <uuid>
"""

import argparse
import uuid
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.services import composer, finder, memory_control, parse, plan
from app.services import recall as recall_service
from app.services import restyle as restyle_service
from app.services import timeref

# Just after the corpus ends. The corpus sits in the past, so resolving
# against the wall clock would make every time expression empty.
NOW = datetime(2026, 6, 17, 9, 0, tzinfo=timezone.utc)

# What the parser should make of a freight request. The check is on intent
# only: topic wording is a judgement call, intent is not.
PARSE_CASES = [
    ("what rate did we agree with Sundaram", "recall"),
    ("when do vendor payments go out", "recall"),
    ("find the message I sent about the Coimbatore depot yesterday", "find_dictation"),
    ("draft a note to the transporter about detention charges", "draft"),
    ("what do you know about loader wages", "memory_control"),
    ("forget everything about the Hosur lane", "memory_control"),
    ("rewrite this for the depot WhatsApp group", "restyle"),
    ("summarise everything about the Sundaram contract", "recall"),
]

# Questions with an answer in the corpus, and a word that has to appear.
ANSWERABLE = [
    ("what rate did we agree with Sundaram", ["eighteen", "18"]),
    ("who runs the Coimbatore depot now", ["kumar"]),
    ("when do we pay vendors", ["fifth", "5th"]),
    ("how long before detention charges start", ["four", "4"]),
    ("what is happening with loader wages", ["eight", "8"]),
    ("how late can we dispatch at night", ["eight", "8"]),
]

# Nothing in a freight corpus covers these. Answering is the failure.
UNANSWERABLE = [
    "what is our diesel bill this quarter",
    "who signed off the warehouse lease",
    "what did the insurer say about the claim",
]


def _check(text: str, needles: list[str]) -> bool:
    lowered = text.lower()
    return any(needle.lower() in lowered for needle in needles)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    args = parser.parse_args()
    user_id = uuid.UUID(args.user_id)

    print("=== parser: does it read this domain's requests")
    right = 0
    for request, expected in PARSE_CASES:
        spec, _ = parse.parse(request)
        ok = spec.intent == expected
        right += ok
        mark = "ok  " if ok else "MISS"
        print(f"  {mark} {expected:15s} got {spec.intent:15s} {request[:44]}")
    print(f"  intent {right}/{len(PARSE_CASES)}")

    print("\n=== recall: questions the corpus can answer")
    answered = correct = 0
    with SessionLocal() as session:
        for question, needles in ANSWERABLE:
            result = recall_service.answer(session, user_id, question, now=NOW)
            answered += result.answered
            hit = result.answered and _check(result.text, needles)
            correct += hit
            print(f"  {'ok  ' if hit else 'MISS'} {question[:46]}")
            if not hit:
                print(f"        {result.text[:88]}")
    print(f"  answered {answered}/{len(ANSWERABLE)}, correct {correct}/{len(ANSWERABLE)}")

    print("\n=== abstention: questions it cannot answer")
    abstained = 0
    with SessionLocal() as session:
        for question in UNANSWERABLE:
            result = recall_service.answer(session, user_id, question, now=NOW)
            abstained += not result.answered
            print(f"  {'ok  ' if not result.answered else 'MISS'} {question[:46]}")
            if result.answered:
                print(f"        invented: {result.text[:80]}")
    print(f"  abstained {abstained}/{len(UNANSWERABLE)}")

    print("\n=== find_dictation")
    with SessionLocal() as session:
        spec, _ = parse.parse("find what I dictated yesterday about the depot")
        when = timeref.resolve(spec.time_expression, now=NOW)
        result = finder.find(session, user_id, now=NOW, when=when, topic=spec.topic)
        print(f"  time={spec.time_expression!r} -> {len(result.found)} found, "
              f"widened={result.widened}")
        for item in result.found[:3]:
            print(f"     {item.event.occurred_at:%d %b %H:%M} {item.event.app:9s} "
                  f"{str(item.event.canonical_text)[:52]}")

    print("\n=== restyle: a real dictation from this corpus")
    with SessionLocal() as session:
        sample = finder.find(session, user_id, now=NOW, limit=1).found
        if sample:
            original = sample[0].event.canonical_text
            styled = restyle_service.restyle(
                session, user_id, original, instruction="make it formal", now=NOW
            )
            print(f"  before : {original[:76]}")
            print(f"  after  : {styled.text[:76]}")
            print(f"  facts kept: {styled.trustworthy}  ({styled.changed[:50]})")

    print("\n=== draft")
    with SessionLocal() as session:
        drafted = composer.compose(
            session, user_id,
            "draft a short note to the depot team about the loader wage increase",
            topic="loader wages", now=NOW,
        )
        print(f"  enough={drafted.enough} cites={len(drafted.citations)}")
        print(f"  {drafted.text[:150] if drafted.enough else drafted.note[:120]}")

    print("\n=== memory_control")
    with SessionLocal() as session:
        view = memory_control.show(session, user_id, topic="the Coimbatore depot", now=NOW)
        print(f"  show -> {len(view.memories)} memories")
        for memory in view.memories[:3]:
            print(f"     [{memory.type}] {memory.content[:62]}")
        refused = memory_control.forget(session, user_id, topic="", now=NOW)
        print(f"  forget with no topic -> refused: {bool(refused.refused)}")
        session.rollback()

    print("\n=== planner")
    spec, _ = parse.parse("find the LR message from yesterday and make it formal")
    made = plan.plan(spec)
    print(f"  {' -> '.join(made.tools)}  ({made.rationale[:60]})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
