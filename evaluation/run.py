"""Run the question set and report what happened.

Three arms over the same 60 questions:

  full         the system as built
  vector_only  no SQL narrowing, similarity decides alone
  no_memory    the model with no retrieval at all

The deltas are the argument. If `full` and `no_memory` score the same, the
memory is decoration; if `full` and `vector_only` score the same, filtering
before searching is complexity that is not paying for itself.

The parse is done once per question and reused across the arms. The arms
differ in retrieval and generation, so holding the reading of the question
fixed is what makes them comparable -- and it saves a model call each.

Results are written after every question. A run that dies at question 50
keeps the first 49, which matters when the daily quota allows one attempt.

    python -m evaluation.run
    python -m evaluation.run --arms full --limit 10
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from app.config import settings
from app.db.session import SessionLocal
from app.llm.client import SMALL, get_client
from app.schemas.recall import AnswerOut
from app.services import asking, parse
from app.services.ablation import NONE, Ablation

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "evaluation" / "questions.yaml"
RESULTS = ROOT / "evaluation" / "results"

ARMS = ("full", "vector_only", "no_memory")

# What a model with no memory is asked. Deliberately fair: it is told to
# decline rather than guess, so a hallucination here is the model's own.
NO_MEMORY_SYSTEM = (
    "Answer the user's question about their own work history. You have no "
    "access to their notes or messages. If you do not know the answer, set "
    "answered to false and say so. Do not guess."
)


# --- scoring ---------------------------------------------------------------


def _matches(text: str, expect: dict) -> tuple[bool, list[str]]:
    """Does this answer contain what it must, and nothing it must not."""
    lowered = (text or "").lower()
    misses: list[str] = []

    for group in expect.get("includes", []):
        if not any(alt.lower() in lowered for alt in group):
            misses.append("missing " + "/".join(group))

    for banned in expect.get("excludes", []):
        if banned.lower() in lowered:
            misses.append(f"stated {banned}, which is superseded")

    return not misses, misses


def _last_day(end: str | None) -> str | None:
    """The last date a half-open range actually covers.

    The resolver returns [start, end): "yesterday" ends at midnight on the
    following day, which is the correct way to express a whole day and the
    wrong thing to compare against an inclusive date. Midnight means the
    range stops before that day began, so the last day covered is the one
    before. Any other time of day is itself the last day.
    """
    if not end:
        return None
    date, _, clock = end.partition("T")
    if clock.startswith("00:00"):
        return (datetime.fromisoformat(end).date() - timedelta(days=1)).isoformat()
    return date


def _window_ok(resolved: dict | None, expect: dict) -> tuple[bool, str]:
    """Did the time expression resolve to the range the question means."""
    want = expect.get("window") or {}
    if resolved is None:
        return False, "no window resolved"

    got_from = (resolved.get("from") or "")[:10] or None
    got_to = _last_day(resolved.get("to"))
    want_from = str(want["from"])[:10] if want.get("from") else None
    want_to = str(want["to"])[:10] if want.get("to") else None

    if got_from == want_from and got_to == want_to:
        return True, ""
    return False, f"resolved {got_from}..{got_to}, expected {want_from}..{want_to}"


def score(question: dict, observed: dict) -> dict:
    """One verdict, with the reason it failed if it did."""
    expect = question.get("expect") or {}
    category = question["category"]
    answered = bool(observed.get("answered"))

    if category == "temporal":
        ok, why = _window_ok(observed.get("window"), expect)
        return {"correct": ok, "why": why}

    if expect.get("answered") is False:
        # Answering is the failure. The text is reported so an invented
        # answer can be read rather than merely counted.
        return {
            "correct": not answered,
            "why": "" if not answered else f"answered: {observed.get('text', '')[:120]}",
        }

    if expect.get("ambiguous"):
        # The name matches two different people. Naming one is the failure
        # the question exists to catch, so answering confidently is wrong and
        # only saying so counts.
        said = (observed.get("text") or "").lower()
        flagged = not answered or any(
            hint in said
            for hint in ("two ", "both ", "which ", "ambiguous", "unclear",
                         "more than one", "cannot tell", "can't tell")
        )
        return {
            "correct": flagged,
            "why": "" if flagged else f"named one without flagging: {said[:90]}",
        }

    if not answered:
        return {"correct": False, "why": "abstained on an answerable question"}

    ok, misses = _matches(observed.get("text", ""), expect)
    if ok and expect.get("min_citations"):
        if observed.get("citations", 0) < expect["min_citations"]:
            return {
                "correct": False,
                "why": f"{observed.get('citations', 0)} citations, "
                       f"wanted {expect['min_citations']}",
            }
    return {"correct": ok, "why": "; ".join(misses)}


# --- the arms --------------------------------------------------------------


def _run_full(session, user_id, question, now, parsed, cuts) -> dict:
    started = time.perf_counter()
    result = asking.run(
        session, user_id, question["question"], now=now, cuts=cuts, parsed=parsed
    )
    session.commit()
    payload = result.as_dict()

    recall = next(
        (step["result"] for step in payload["steps"] if step["tool"] == "recall"),
        {},
    )
    return {
        "answered": bool(recall.get("answered")),
        "text": recall.get("answer", ""),
        "citations": len(recall.get("citations") or []),
        "window": payload["understood_as"].get("time"),
        "trace_id": payload["trace_id"],
        "latency_ms": int((time.perf_counter() - started) * 1000),
        "intent": payload["understood_as"]["intent"],
    }


def _run_no_memory(question, now) -> dict:
    """The model alone. No retrieval, no sources, no profile."""
    started = time.perf_counter()
    try:
        completion = get_client().structured(
            prompt=f"Today is {now:%d %B %Y}.\n\nQuestion: {question['question']}",
            schema=AnswerOut,
            system=NO_MEMORY_SYSTEM,
            tier=SMALL,
        )
        value = completion.value
        return {
            "answered": bool(value.answered),
            "text": value.answer,
            "citations": 0,
            "window": None,
            "trace_id": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "cost_usd": completion.usage.cost_usd,
        }
    except Exception as exc:  # noqa: BLE001 - one arm failing must not end the run
        return {
            "answered": False,
            "text": "",
            "error": str(exc)[:200],
            "citations": 0,
            "window": None,
            "trace_id": None,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }


# --- the run ---------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", default=str(QUESTIONS))
    parser.add_argument("--user-id", default=None)
    parser.add_argument(
        "--now", default=None, help="reference clock; questions may override it"
    )
    parser.add_argument("--arms", nargs="*", default=list(ARMS), choices=ARMS)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--category", default=None)
    parser.add_argument(
        "--rescore",
        default=None,
        help=(
            "re-mark a finished run from its stored results. What the system "
            "answered is already recorded, so a corrected marking scheme "
            "costs nothing to apply and the answers cannot drift underneath it"
        ),
    )
    args = parser.parse_args(argv)

    if args.rescore:
        return _rescore(Path(args.rescore), Path(args.questions))

    spec = yaml.safe_load(Path(args.questions).read_text())
    questions = spec["questions"]
    if args.category:
        questions = [q for q in questions if q["category"] == args.category]
    if args.limit:
        questions = questions[: args.limit]

    user_id = uuid.UUID(args.user_id) if args.user_id else settings.default_user_id
    default_now = _clock(args.now or spec["meta"]["default_now"])

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = RESULTS / stamp
    out.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, Any]] = []
    print(f"{len(questions)} questions x {len(args.arms)} arms -> {out}")

    with SessionLocal() as session:
        for index, question in enumerate(questions, 1):
            now = _clock(question["now"]) if question.get("now") else default_now

            # One reading of the question, shared by every arm that needs it.
            parsed = parse.parse(question["question"]) if "full" in args.arms \
                or "vector_only" in args.arms else None

            row: dict[str, Any] = {
                "id": question["id"],
                "category": question["category"],
                "question": question["question"],
                "now": now.isoformat(),
                "arms": {},
            }

            for arm in args.arms:
                if arm == "no_memory":
                    observed = _run_no_memory(question, now)
                else:
                    cuts = (
                        Ablation(skip_filters=True) if arm == "vector_only" else NONE
                    )
                    observed = _run_full(
                        session, user_id, question, now, parsed, cuts
                    )
                verdict = score(question, observed)
                row["arms"][arm] = {**observed, **verdict}

            rows.append(row)
            _write(out, spec, rows, args.arms)

            mark = "".join(
                "." if row["arms"][a]["correct"] else "X" for a in args.arms
            )
            print(f"  [{index:2d}/{len(questions)}] {mark} {question['id']:8s} "
                  f"{question['question'][:52]}")

    print(f"\nwrote {out}/results.json and report.md")
    return 0


def _rescore(directory: Path, questions_path: Path) -> int:
    """Apply the current marking scheme to a run that already happened.

    Kept separate from the run so it is obvious that no answer changed: only
    the verdicts are recomputed, from the same stored text and windows.
    """
    payload = json.loads((directory / "results.json").read_text())
    spec = yaml.safe_load(questions_path.read_text())
    by_id = {q["id"]: q for q in spec["questions"]}

    changed = 0
    for row in payload["questions"]:
        question = by_id.get(row["id"])
        if question is None:
            continue
        for arm, observed in row["arms"].items():
            was = observed.get("correct")
            observed.update(score(question, observed))
            changed += observed["correct"] != was

    _write(directory, spec, payload["questions"], payload["arms"])
    print(f"re-marked {directory}, {changed} verdicts changed")
    return 0


def _clock(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _write(out: Path, spec: dict, rows: list[dict], arms: list[str]) -> None:
    """Save after every question, so a crash keeps what has been measured."""
    summary = _summarise(rows, arms)
    (out / "results.json").write_text(
        json.dumps(
            {"meta": spec["meta"], "arms": arms, "summary": summary, "questions": rows},
            indent=2,
            # The spec's dates parse as date objects, which json will not take.
            default=str,
        )
    )
    (out / "report.md").write_text(_report(summary, rows, arms))


def _summarise(rows: list[dict], arms: list[str]) -> dict:
    out: dict[str, Any] = {"overall": {}, "by_category": {}}
    categories = sorted({row["category"] for row in rows})

    for arm in arms:
        got = [row["arms"][arm] for row in rows if arm in row["arms"]]
        correct = sum(1 for r in got if r["correct"])
        latencies = sorted(r.get("latency_ms", 0) for r in got)
        out["overall"][arm] = {
            "n": len(got),
            "correct": correct,
            "accuracy": round(correct / len(got), 3) if got else 0.0,
            "latency_p50_ms": _pct(latencies, 0.50),
            "latency_p95_ms": _pct(latencies, 0.95),
        }

    for category in categories:
        subset = [row for row in rows if row["category"] == category]
        out["by_category"][category] = {}
        for arm in arms:
            got = [row["arms"][arm] for row in subset if arm in row["arms"]]
            correct = sum(1 for r in got if r["correct"])
            out["by_category"][category][arm] = {
                "n": len(got),
                "correct": correct,
                "accuracy": round(correct / len(got), 3) if got else 0.0,
            }

    # Abstention is the number the brief puts a floor under, so it is
    # reported on its own rather than left inside the category table.
    refusals = [r for r in rows if r["category"] in ("unanswerable", "ignored")]
    for arm in arms:
        got = [r["arms"][arm] for r in refusals if arm in r["arms"]]
        held = sum(1 for r in got if not r.get("answered"))
        out["overall"][arm]["abstention_rate"] = (
            round(held / len(got), 3) if got else 0.0
        )
    return out


def _pct(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    return int(values[min(len(values) - 1, int(len(values) * fraction))])


def _report(summary: dict, rows: list[dict], arms: list[str]) -> str:
    lines = ["# Evaluation", "", "## Ablations", ""]
    lines.append("| arm | accuracy | correct | abstention | p50 ms | p95 ms |")
    lines.append("|---|---|---|---|---|---|")
    for arm in arms:
        s = summary["overall"][arm]
        lines.append(
            f"| {arm} | {s['accuracy']:.0%} | {s['correct']}/{s['n']} | "
            f"{s['abstention_rate']:.0%} | {s['latency_p50_ms']} | "
            f"{s['latency_p95_ms']} |"
        )

    lines += ["", "## By category", "", "| category | " +
              " | ".join(arms) + " |", "|---|" + "---|" * len(arms)]
    for category, per_arm in summary["by_category"].items():
        cells = " | ".join(
            f"{per_arm[a]['correct']}/{per_arm[a]['n']}" for a in arms
        )
        lines.append(f"| {category} | {cells} |")

    failures = [
        (row, row["arms"]["full"])
        for row in rows
        if "full" in row["arms"] and not row["arms"]["full"]["correct"]
    ]
    lines += ["", f"## Failures in the full system ({len(failures)})", ""]
    if failures:
        lines.append("| id | question | why | trace |")
        lines.append("|---|---|---|---|")
        for row, arm in failures:
            lines.append(
                f"| {row['id']} | {row['question'][:44]} | {arm['why'][:70]} | "
                f"{(arm.get('trace_id') or '')[:8]} |"
            )
    else:
        lines.append("None, which for a set containing known-failing "
                     "questions would itself be suspicious.")
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
