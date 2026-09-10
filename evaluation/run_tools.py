"""Measure the four tools the recall evaluation leaves out.

The headline number covers recall only. Finding a dictation, rewriting one,
drafting from several and editing memory are not in it, so as it stands the
system is claimed to do four things it has never been measured doing.

Scored on properties with a right answer -- did the rewrite keep the price,
does every draft trace to a source, does deletion refuse when it should --
rather than on whether the prose reads well, which would need a judge model
and would produce a number nobody could check.

Deletion is tested against the real corpus inside a transaction that is
rolled back. Measuring a destructive operation must not be destructive.

    python -m evaluation.run_tools
"""

from __future__ import annotations

import argparse
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from app.config import settings
from app.db.session import SessionLocal
from app.services import composer, finder, memory_control, parse, timeref
from app.services import restyle as restyle_service

ROOT = Path(__file__).resolve().parent.parent
CASES = ROOT / "evaluation" / "tools.yaml"
RESULTS = ROOT / "evaluation" / "results"


def _clock(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def check_routing(cases: list[dict]) -> list[dict]:
    """Does the request reach the right tool at all."""
    rows = []
    for case in cases:
        parsed = parse.parse(case["request"])
        got = parsed.spec.intent
        rows.append({
            "request": case["request"],
            "expected": case["intent"],
            "got": got,
            "correct": got == case["intent"],
        })
    return rows


def check_find(session, user_id, cases: list[dict], default_now: datetime) -> list[dict]:
    rows = []
    for case in cases:
        now = _clock(case["now"]) if case.get("now") else default_now
        parsed = parse.parse(case["request"])
        spec = parsed.spec
        when = timeref.resolve(spec.time_expression, now=now)
        found = finder.find(
            session, user_id, now=now, when=when, apps=spec.apps or None,
            topic=spec.topic if spec.time_expression is None else "",
        )
        texts = [str(item.event.canonical_text).lower() for item in found.found]
        apps = {item.event.app for item in found.found}
        expect = case["expect"]

        why = ""
        correct = bool(found.found)
        if not correct:
            why = "found nothing"
        elif "contains" in expect:
            correct = any(expect["contains"].lower() in text for text in texts)
            why = "" if correct else f"none of {len(texts)} mention {expect['contains']!r}"
        elif "app" in expect:
            correct = apps == {expect["app"]}
            why = "" if correct else f"returned apps {sorted(apps)}"

        rows.append({
            "id": case["id"], "request": case["request"], "found": len(found.found),
            "widened": found.widened, "correct": correct, "why": why,
        })
    return rows


def check_restyle(session, user_id, cases: list[dict], now: datetime) -> list[dict]:
    rows = []
    for case in cases:
        result = restyle_service.restyle(
            session, user_id, case["text"],
            instruction=case.get("instruction"), now=now,
        )
        kept = [
            needle for needle in case["expect"]["keeps"]
            if needle.lower() in result.text.lower()
        ]
        missing = set(case["expect"]["keeps"]) - set(kept)
        # Two ways to be right: rewrite and keep the facts, or notice the
        # rewrite lost one and hand back the original. Both are safe.
        correct = not missing and result.trustworthy
        rows.append({
            "id": case["id"], "before": case["text"][:70], "after": result.text[:70],
            "trustworthy": result.trustworthy, "dropped": result.dropped_facts,
            "correct": correct,
            "why": "" if correct else f"lost {sorted(missing)}" if missing else "guard fired",
        })
    return rows


def check_draft(session, user_id, cases: list[dict], now: datetime) -> list[dict]:
    rows = []
    for case in cases:
        drafted = composer.compose(
            session, user_id, case["request"], topic=case.get("topic", ""), now=now
        )
        expect = case["expect"]
        correct = drafted.enough == expect["enough"]
        why = ""
        if not correct:
            why = ("invented a draft with no support" if drafted.enough
                   else "withdrew a draft it had sources for")
        elif expect["enough"] and len(drafted.citations) < expect.get("min_citations", 1):
            correct = False
            why = f"{len(drafted.citations)} citations"
        rows.append({
            "id": case["id"], "request": case["request"][:52],
            "enough": drafted.enough, "citations": len(drafted.citations),
            "correct": correct, "why": why,
        })
    return rows


def check_memory(session, user_id, cases: list[dict], now: datetime) -> list[dict]:
    """Deletion included, inside a transaction that is rolled back."""
    rows = []
    for case in cases:
        expect = case["expect"]
        if case["action"] == "show":
            view = memory_control.show(session, user_id, topic=case["topic"], now=now)
            correct = len(view.memories) >= expect.get("min_memories", 1)
            why = "" if correct else f"showed {len(view.memories)}"
            detail = f"{len(view.memories)} memories"
        else:
            view = memory_control.forget(session, user_id, topic=case["topic"], now=now)
            if expect.get("refused"):
                correct = bool(view.refused)
                why = "" if correct else f"deleted {len(view.deleted)} instead of refusing"
            else:
                correct = bool(view.deleted) and not view.refused
                why = "" if correct else (view.refused or "deleted nothing")
            detail = (f"refused: {view.refused[:48]}" if view.refused
                      else f"deleted {len(view.deleted)}")
            # Never keep a deletion made to measure one.
            session.rollback()

        rows.append({
            "id": case["id"], "action": case["action"], "topic": case["topic"][:40],
            "detail": detail, "correct": correct, "why": why,
        })
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", default=str(CASES))
    parser.add_argument("--user-id", default=None)
    args = parser.parse_args(argv)

    spec = yaml.safe_load(Path(args.cases).read_text())
    user_id = uuid.UUID(args.user_id) if args.user_id else settings.default_user_id
    now = _clock(spec["meta"]["default_now"])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = RESULTS / f"tools-{stamp}"
    out.mkdir(parents=True, exist_ok=True)

    sections: dict[str, list[dict]] = {}
    with SessionLocal() as session:
        sections["routing"] = check_routing(spec["routing"])
        sections["find"] = check_find(session, user_id, spec["find"], now)
        sections["restyle"] = check_restyle(session, user_id, spec["restyle"], now)
        sections["draft"] = check_draft(session, user_id, spec["draft"], now)
        sections["memory"] = check_memory(session, user_id, spec["memory"], now)
        session.rollback()

    total = correct = 0
    for name, rows in sections.items():
        got = sum(1 for row in rows if row["correct"])
        total += len(rows)
        correct += got
        print(f"\n=== {name}: {got}/{len(rows)}")
        for row in rows:
            mark = "ok  " if row["correct"] else "MISS"
            label = row.get("id") or row.get("request", "")[:36]
            print(f"  {mark} {label:10s} {row.get('why') or _describe(row)}")

    print(f"\ntotal {correct}/{total}")
    (out / "tools.json").write_text(
        json.dumps({"sections": sections, "correct": correct, "total": total},
                   indent=2, default=str)
    )
    print(f"wrote {out}/tools.json")
    return 0


def _describe(row: dict) -> str:
    for key in ("detail", "after", "got"):
        if key in row:
            return str(row[key])[:60]
    if "citations" in row:
        return f"enough={row['enough']} cites={row['citations']}"
    if "found" in row:
        return f"{row['found']} found"
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
