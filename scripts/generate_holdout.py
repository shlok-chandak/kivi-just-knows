"""Generate the frozen held-out set, and measure the rules against it.

Two jobs in one script, deliberately. Generating the file and scoring it in
the same place makes it obvious that the scoring never feeds back into the
rules -- there is nothing here that edits app/services.

    python -m scripts.generate_holdout            # regenerate and score
    python -m scripts.generate_holdout --score    # score the existing file
"""

from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.services.junk import classify
from app.services.sensitive import detect_sensitive
from scripts.generate_corpus import IST, to_asr
from scripts.holdout_content import (
    EXPECTED_MEMORIES,
    EXPECTED_PREFERENCES,
    FOLLOW_UPS,
    JUNK,
    LOGISTICS_CHATTER,
    NEAR_MISSES,
    PERSONA,
    RETRIES,
    SENSITIVE,
    WORK,
)

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "corpus" / "holdout.jsonl"
OUT_TRUTH = ROOT / "corpus" / "holdout_truth.json"

SEED = 77
START = datetime(2026, 6, 15, 9, 0, tzinfo=IST)

WINDOWS = {
    "slack": "#ops (Haulr) - Slack",
    "whatsapp": "Ravi - WhatsApp",
    "gmail": "Sundaram Fasteners - Gmail",
    "notion": "Depot handbook - Notion",
}

NOISE = {
    "number_words": 0.5,
    "name_misrecognition": 0.15,
    "disfluency_insertion": 0.28,
    "repetition": 0.08,
}


def build() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    rng = random.Random(SEED)
    items: list[tuple[str, str, str | None]] = []

    items += [("stored", line, None) for line in WORK]
    items += [("stored", line, None) for line in FOLLOW_UPS]
    items += [("stored", line, None) for line in LOGISTICS_CHATTER]
    items += [("near_miss", line, None) for line in NEAR_MISSES]
    for category, lines in SENSITIVE.items():
        items += [("refused", line, category) for line in lines]
    for reason, lines in JUNK.items():
        items += [("not_embedded", line, reason) for line in lines]
    items += [("retry", line, "superseded_by_retry") for line in RETRIES]

    rng.shuffle(items)

    records: list[dict[str, Any]] = []
    truth: list[dict[str, Any]] = []
    when = START

    for index, (expect, text, label) in enumerate(items, start=1):
        # A retry needs the take it repeats, seconds earlier in the same place.
        if expect == "retry":
            when += timedelta(minutes=rng.randint(3, 25))
            records.append(_record(len(records) + 1, when, "slack", text, rng))
            when += timedelta(seconds=rng.randint(8, 25))
        else:
            when += timedelta(minutes=rng.randint(2, 40))

        app = rng.choice(["slack", "slack", "whatsapp", "gmail", "notion"])
        record = _record(len(records) + 1, when, app, text, rng)

        if expect == "not_embedded" and label == "discarded_by_user":
            record["committed_text"] = ""
        elif expect == "not_embedded" and label == "low_asr_confidence":
            record["asr_confidence"] = round(rng.uniform(0.2, 0.5), 2)
        elif expect == "not_embedded" and label == "implausible_timing":
            record["duration_ms"] = rng.randint(9000, 13000)

        records.append(record)
        truth.append({
            "external_id": record["external_id"],
            "expect": "not_embedded" if expect == "retry" else expect,
            "sensitive_category": label if expect == "refused" else None,
            "ignore_reason": label if expect in ("not_embedded", "retry") else None,
        })

    return records, {
        "frozen": True,
        "note": (
            "Held out. No rule may be changed in response to a failure here, "
            "and this file may not be edited to make a rule pass."
        ),
        "persona": PERSONA,
        "records": len(records),
        "expected_memories": EXPECTED_MEMORIES,
        "expected_preferences": EXPECTED_PREFERENCES,
        "per_record": truth,
    }


def _record(
    index: int, when: datetime, app: str, text: str, rng: random.Random
) -> dict[str, Any]:
    record: dict[str, Any] = {
        "external_id": f"h_{index:04d}",
        "occurred_at": when.isoformat(),
        "app": app,
        "formatted_text": text,
        "raw_asr": to_asr(text, rng, NOISE),
        "asr_confidence": round(rng.uniform(0.83, 0.97), 2),
        "duration_ms": max(900, int(len(text.split()) * rng.uniform(380, 620))),
        "metadata": {"device": "windows", "locale": "en-IN"},
    }
    if rng.random() < 0.65:
        record["window_title"] = WINDOWS.get(app, "Untitled")
    return record


# --- scoring ----------------------------------------------------------------


def score(records: list[dict], truth: dict[str, Any]) -> None:
    """Measure the deterministic rules. Nothing here writes to app/."""
    by_id = {r["external_id"]: r for r in records}

    sensitive_missed: list[tuple[str, str]] = []
    sensitive_caught = 0
    wrong_category: list[tuple[str, str, str]] = []
    false_refusals: list[tuple[str, str]] = []

    junk_missed: list[tuple[str, str]] = []
    junk_caught = 0

    for row in truth["per_record"]:
        record = by_id[row["external_id"]]
        text = record["formatted_text"]
        found = detect_sensitive(text)

        if row["expect"] == "refused":
            if found is None:
                sensitive_missed.append((row["sensitive_category"], text))
            else:
                sensitive_caught += 1
                if found != row["sensitive_category"]:
                    wrong_category.append((row["sensitive_category"], found, text))
        elif found is not None:
            false_refusals.append((found, text))

        if row["expect"] == "not_embedded":
            reason = classify(
                text,
                committed_text=record.get("committed_text"),
                asr_confidence=record.get("asr_confidence"),
                duration_ms=record.get("duration_ms"),
            )
            if reason is None and row["ignore_reason"] != "superseded_by_retry":
                junk_missed.append((row["ignore_reason"], text))
            else:
                junk_caught += 1

    expected_sensitive = sum(
        1 for r in truth["per_record"] if r["expect"] == "refused"
    )
    expected_junk = sum(
        1 for r in truth["per_record"] if r["expect"] == "not_embedded"
    )

    print(f"\nHELD OUT — {truth['records']} records, {PERSONA['domain']}")
    print("=" * 66)
    print(f"sensitive caught   {sensitive_caught}/{expected_sensitive}"
          f"   ({sensitive_caught / expected_sensitive:.0%} recall)")
    print(f"false refusals     {len(false_refusals)}"
          f"   (each one loses a dictation permanently)")
    print(f"junk caught        {junk_caught}/{expected_junk}")

    if sensitive_missed:
        print("\nMISSED — stored when it should have been refused:")
        for category, text in sensitive_missed:
            print(f"  [{category}] {text}")
    if wrong_category:
        print("\nCAUGHT, WRONG CATEGORY:")
        for want, got, text in wrong_category:
            print(f"  want {want}, got {got}: {text[:56]}")
    if false_refusals:
        print("\nFALSE REFUSALS — ordinary work wrongly discarded:")
        for category, text in false_refusals:
            print(f"  [{category}] {text}")
    if junk_missed:
        print("\nJUNK NOT CAUGHT:")
        for reason, text in junk_missed:
            print(f"  [{reason}] {text}")

    print("\nThese numbers are the honest ones. The in-sample corpus reports "
          "100% because\nthe rules were extended until it did. Do not fix "
          "anything above without\nfirst deciding the rule is wrong in "
          "general -- and if you do, this set is\nno longer held out.")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--score", action="store_true",
                        help="score the existing file without regenerating")
    args = parser.parse_args(argv)

    if args.score and OUT.exists():
        records = [json.loads(line) for line in OUT.read_text().splitlines()]
        truth = json.loads(OUT_TRUTH.read_text())
    else:
        records, truth = build()
        with OUT.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        OUT_TRUTH.write_text(json.dumps(truth, indent=2, ensure_ascii=False))
        print(f"wrote {len(records)} records -> {OUT}")

    score(records, truth)


if __name__ == "__main__":
    main()
