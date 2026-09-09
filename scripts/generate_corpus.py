"""Build the 500-record corpus from corpus/spec.yaml and the authored content.

Deterministic: seeded once, so the same spec always produces the same corpus
byte for byte. That matters because every evaluation number is measured
against a specific corpus, and a corpus that drifts between runs makes those
numbers meaningless.

Three things happen to each line. It is placed on a timeline of sittings, it
is degraded into what a recogniser would have heard, and it is edited the way
the user edits -- which is the only evidence of how they actually write.

    python -m scripts.generate_corpus
    python -m scripts.generate_corpus --records 200 --seed 7
"""

from __future__ import annotations

import argparse
import json
import random
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from scripts.corpus_content import (
    FRAME_FOLLOWUP,
    FRAME_LOGISTICS,
    FRAME_LONG,
    FRAME_TAIL,
    FRAME_UPDATE,
    JUNK,
    NEAR_MISSES,
    PRIYA_DISAMBIGUATION,
    RETRIES,
    SCRIPTED,
    SENSITIVE,
    SLOTS,
    STATED_PREFERENCES,
)

ROOT = Path(__file__).resolve().parent.parent
SPEC = ROOT / "corpus" / "spec.yaml"
OUT_CORPUS = ROOT / "corpus" / "corpus.jsonl"
OUT_TRUTH = ROOT / "corpus" / "ground_truth.json"

IST = timezone(timedelta(hours=5, minutes=30))

# Window titles per context key. Never parsed by the system -- hashed at
# ingest -- but written realistically so the file reads like real capture.
WINDOWS = {
    "pricing": "#pricing (Nomi) - Slack",
    "eng": "#engineering (Nomi) - Slack",
    "general": "#general (Nomi) - Slack",
    "design": "#design (Nomi) - Slack",
    "legal": "#legal (Nomi) - Slack",
    "adi": "Aditya Sharma - WhatsApp",
    "priya": "Priya Menon - WhatsApp",
    "abc": "Pro tier pricing - ABC Inc - Gmail",
    "trellis": "DPA review - Trellis Health - Gmail",
    "board": "Monthly update - investors - Gmail",
    "soc2": "SOC2 - Nomad Audit - Gmail",
    "pricingdoc": "Pricing v3 - Notion",
    "gatewaydoc": "Gateway migration - Notion",
    "ops": "Ops handbook - Notion",
    "rules": "NOM-214 Inbox rules v2 - Linear",
}

PEOPLE = ["Aditya Sharma", "Pranav Iyer", "Priya Menon", "Kavya Reddy", "Rohit Bhatia"]
PRONOUNS = {
    "Aditya Sharma": "he",
    "Pranav Iyer": "he",
    "Priya Menon": "she",
    "Kavya Reddy": "she",
    "Rohit Bhatia": "he",
}
SHORT_FORM = {"Aditya Sharma": "Aditya", "Pranav Iyer": "Pranav",
              "Priya Menon": "Priya", "Kavya Reddy": "Kavya",
              "Rohit Bhatia": "Rohit"}


# --- speech recognition -----------------------------------------------------

_NUMBER_WORDS = {
    "299": "two ninety nine", "349": "three forty nine", "499": "four ninety nine",
    "250": "two fifty", "18": "eighteen", "40,000": "forty thousand",
    "3.1": "three point one", "9:30": "nine thirty", "10": "ten",
}
_MISHEARD = {
    "aditya": "additya", "pranav": "pranam", "priya": "priyah",
    "kavya": "kavia", "rohit": "rohith", "cashfree": "cash free",
    "razorpay": "razor pay", "trellis": "trelis", "lumen": "loomen",
    "soc2": "sock two", "nomi": "normi",
}
_DISFLUENCY = ["um", "uh", "so", "like", "you know", "I mean"]


def to_asr(text: str, rng: random.Random, noise: dict[str, Any]) -> str:
    """What the recogniser would have produced.

    Degraded in the ways recognisers actually fail: numerals arrive as words,
    unfamiliar names come out wrong, disfluencies survive, and nothing is
    punctuated or capitalised.
    """
    working = text
    for figure, spoken in _NUMBER_WORDS.items():
        if figure in working and rng.random() < noise["number_words"]:
            working = working.replace(figure, spoken)

    working = working.lower()
    working = working.replace("₹", "").replace("%", " percent")
    working = re.sub(r"[.,!?;:\"()\[\]]", "", working)

    words = working.split()
    out: list[str] = []
    for word in words:
        if word in _MISHEARD and rng.random() < noise["name_misrecognition"]:
            word = _MISHEARD[word]
        if rng.random() < noise["repetition"] * 0.5:
            out.append(word)
        out.append(word)

    if out and rng.random() < noise["disfluency_insertion"]:
        out.insert(rng.randrange(min(3, len(out))), rng.choice(_DISFLUENCY))

    return " ".join(out).strip()


# --- how the user edits -----------------------------------------------------
#
# Each operator changes punctuation, casing or a fixed token -- never a word.
# That is the guard rule in PROJECT_CONTEXT §16.3, and it is what separates a
# preference learner from a noise accumulator.

def _drop_terminal_period(text: str) -> str | None:
    return text[:-1] if text.endswith(".") else None


def _lowercase_opener(text: str) -> str | None:
    if text and text[0].isupper() and not text.split()[0] in {w for w in PEOPLE}:
        first = text.split(" ", 1)[0]
        if first.rstrip(".,").lower() in _MISHEARD or first[1:].islower() is False:
            return None
        return text[0].lower() + text[1:]
    return None


def _drop_exclamation(text: str) -> str | None:
    return text[:-1] + "." if text.endswith("!") else None


def _expand_contractions(text: str) -> str | None:
    pairs = [("don't", "do not"), ("can't", "cannot"), ("won't", "will not"),
             ("it's", "it is"), ("we're", "we are"), ("they're", "they are"),
             ("I'll", "I will"), ("doesn't", "does not"), ("isn't", "is not"),
             ("that's", "that is"), ("here's", "here is")]
    out = text
    for short, long in pairs:
        out = out.replace(short, long)
    return out if out != text else None


def _drop_greeting(text: str) -> str | None:
    match = re.match(r"^(Hi|Hey|Hello)[ ,]+", text)
    if not match:
        return None
    rest = text[match.end():]
    return rest[0].upper() + rest[1:] if rest else None


def _insert_emoji(text: str) -> str | None:
    return text + " 👍" if not text.endswith("👍") else None


def _set_signoff(text: str) -> str | None:
    return text + "\n\nRhea" if "\n" not in text else None


def _drop_hedge(text: str) -> str | None:
    for hedge in ("I think ", "I guess ", "maybe ", "possibly ", "sort of "):
        if hedge in text:
            out = text.replace(hedge, "", 1)
            return out[0].upper() + out[1:] if out else None
    return None


def _bulletize(text: str) -> str | None:
    return "- " + text if not text.startswith("- ") else None


def _keep_filler(text: str) -> str | None:
    return None  # the user keeping filler means no edit at all


OPERATORS = {
    "terminal_period.drop": _drop_terminal_period,
    "sentence_initial.lowercase": _lowercase_opener,
    "exclamation.drop": _drop_exclamation,
    "contraction.expand": _expand_contractions,
    "greeting.drop": _drop_greeting,
    "emoji.insert": _insert_emoji,
    "signoff.set": _set_signoff,
    "hedge.drop": _drop_hedge,
    "list.bulletize": _bulletize,
    "filler.keep": _keep_filler,
}

STRENGTH = {"strong": 0.75, "moderate": 0.4, "weak": 0.15}

# Word-token substitutions. These change what was said, so §16.3 must reject
# them as content corrections rather than learning a style from them.
CORRECTIONS = [
    ("Tuesday", "Thursday"), ("Thursday", "Friday"), ("October", "November"),
    ("299", "349"), ("Pranav", "Priya"), ("three", "four"), ("morning", "afternoon"),
]


def apply_edits(
    text: str, app: str, rng: random.Random, edit_prefs: list[dict[str, Any]]
) -> tuple[str | None, str | None]:
    """What the user kept, and which operator explains it.

    Returns (committed_text, operator). A None operator with changed text is a
    content correction: the same shape, but a word moved, so it is evidence
    about facts and not about style.
    """
    if rng.random() < 0.04:
        for before, after in CORRECTIONS:
            if before in text:
                return text.replace(before, after, 1), None

    candidates = [
        pref for pref in edit_prefs
        if app in pref["contexts"] and rng.random() < STRENGTH[pref["strength"]]
    ]
    rng.shuffle(candidates)

    working = text
    used: str | None = None
    for pref in candidates:
        changed = OPERATORS[pref["operator"]](working)
        if changed is not None:
            working = changed
            used = pref["operator"]

    if working == text:
        # Most dictations are accepted as produced.
        return (text, None) if rng.random() < 0.55 else (None, None)
    return working, used


# --- the timeline -----------------------------------------------------------


def working_slots(start: datetime, day: int, rng: random.Random) -> list[datetime]:
    """Times of day someone actually dictates: clustered, not uniform."""
    base = start + timedelta(days=day)
    if base.weekday() >= 5 and rng.random() < 0.8:
        return []

    clusters = rng.sample([9, 11, 14, 16, 18, 21], k=rng.randint(2, 4))
    slots: list[datetime] = []
    for hour in sorted(clusters):
        for _ in range(rng.randint(1, 4)):
            slots.append(
                base.replace(hour=hour, minute=rng.randrange(60),
                             second=rng.randrange(60))
            )
    return sorted(slots)


class Namer:
    """Decides whether a person is named or referred to.

    Named the first time they come up in a sitting, then a pronoun. This is
    the single rule that keeps the corpus from reading like minutes.
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()

    def new_sitting(self) -> None:
        self.seen.clear()

    def refer_to(self, person: str, rng: random.Random) -> str:
        if person not in self.seen:
            self.seen.add(person)
            return SHORT_FORM.get(person, person)
        if rng.random() < 0.08:
            return SHORT_FORM.get(person, person)
        return PRONOUNS.get(person, "they")


# --- assembly ---------------------------------------------------------------


def build(spec: dict[str, Any], target: int, seed: int) -> tuple[list[dict], dict]:
    rng = random.Random(seed)
    noise = spec["asr_noise"]
    edit_prefs = spec["edit_preferences"]
    start = datetime.fromisoformat(str(spec["meta"]["start"])).replace(tzinfo=IST)

    namer = Namer()
    records: list[dict[str, Any]] = []
    truth_records: list[dict[str, Any]] = []

    scripted_by_day: dict[int, list[dict]] = {}
    for beat in SCRIPTED:
        day = (beat["week"] - 1) * 7 + beat.get("day", 1)
        scripted_by_day.setdefault(day, []).append(beat)
    for pref in STATED_PREFERENCES:
        day = (pref["week"] - 1) * 7 + 2
        scripted_by_day.setdefault(day, []).append({**pref, "kind": "preference"})
    for beat in PRIYA_DISAMBIGUATION:
        day = (beat["week"] - 1) * 7 + beat.get("day", 1)
        scripted_by_day.setdefault(day, []).append({**beat, "kind": "fact"})

    # Refused and junk material, spread across the span rather than clustered.
    specials: list[tuple[str, str, str | None]] = []
    for category, lines in SENSITIVE.items():
        specials += [("sensitive", line, category) for line in lines]
    for reason, lines in JUNK.items():
        specials += [("junk", line, reason) for line in lines]
    specials += [("near_miss", line, None) for line in NEAR_MISSES]
    specials += [("retry", line, "superseded_by_retry") for line in RETRIES]
    rng.shuffle(specials)

    apps = list(spec["apps"].keys())
    weights = list(spec["apps"].values())
    counter = 0
    day = 0

    span = spec["meta"]["span_days"]
    while day < span and (len(records) < target or scripted_by_day or specials):
        for slot in working_slots(start, day, rng):
            namer.new_sitting()

            # Unconditional. A dropped beat means a decision arc silently
            # loses a step, and the corpus stops testing the supersession it
            # was built to test.
            beats = scripted_by_day.pop(day, [])
            app = rng.choices(apps, weights=weights, k=1)[0]
            context = rng.choice([k for k, v in WINDOWS.items()
                                  if _app_of(k) == app] or [None])

            sitting: list[dict[str, Any]] = []
            for beat in beats:
                sitting.append(_from_beat(beat, namer, rng))

            # Refused and junk material is spread thinly, but must all land:
            # a category with no record in the corpus is a rule with no test.
            budget = max(1, (target - len(records)) // max(1, (span - day) * 3))
            for _ in range(rng.randint(1, 4)):
                if specials and (rng.random() < 0.14 or len(specials) > budget):
                    special = specials.pop()
                    sitting.append(_from_special(special, namer, rng))
                    # A retry is the same words a few seconds later, so both
                    # halves have to be in the same sitting for the gate to
                    # have anything to compare against.
                    if special[0] == "retry":
                        sitting.insert(
                            -1, {"text": special[1], "special": "retry_first"}
                        )
                else:
                    sitting.append(_from_filler(namer, rng))

            last_when = slot
            for offset, item in enumerate(sitting):
                if len(records) >= target and not item.get("asserts") \
                        and not item.get("special"):
                    continue
                counter += 1
                if item.get("special") == "retry":
                    # Seconds after the take it is repeating, or the gap rule
                    # will not see it as the same attempt.
                    when = last_when + timedelta(seconds=rng.randint(8, 30))
                else:
                    when = slot + timedelta(
                        minutes=offset * rng.randint(1, 6),
                        seconds=rng.randrange(50),
                    )
                last_when = when
                use_app = item.get("app") or app
                use_ctx = item.get("ctx", context)
                record, truth = _emit(
                    counter, when, use_app, use_ctx, item, rng, noise, edit_prefs, spec
                )
                records.append(record)
                if truth:
                    truth_records.append(truth)
        day += 1

    return records, _ground_truth(spec, records, truth_records)


def _app_of(context_key: str) -> str:
    title = WINDOWS[context_key]
    for app in ("Slack", "WhatsApp", "Gmail", "Notion", "Linear"):
        if title.endswith(app):
            return app.lower()
    return "notes"


_SLOT = re.compile(r"\{(\w+)\}")


def _fill(text: str, person: str | None, namer: Namer, rng: random.Random) -> str:
    """Resolve every slot in a frame.

    `{who}` is special: a name the first time that person comes up in the
    sitting, a pronoun afterwards. Everything else draws from SLOTS, which is
    what gives five hundred records their variety without five hundred
    hand-written lines.
    """

    def replace(match: re.Match[str]) -> str:
        slot = match.group(1)
        if slot == "who":
            who = person or rng.choice(PEOPLE)
            return namer.refer_to(who, rng)
        if slot == "n":
            return rng.choice(["five", "ten", "fifteen", "twenty", "half an hour"])
        return rng.choice(SLOTS[slot])

    filled = _SLOT.sub(replace, text)
    # Only a line that began with a slot needs recapitalising. A beat written
    # in lowercase was written that way on purpose -- that is how the user
    # types in casual apps, and forcing a capital would erase the signal.
    if filled and text.startswith("{"):
        filled = filled[0].upper() + filled[1:]
    return filled


def _from_beat(beat: dict, namer: Namer, rng: random.Random) -> dict[str, Any]:
    return {
        "text": _fill(beat["text"], beat.get("person"), namer, rng),
        "app": beat.get("app"),
        "ctx": beat.get("ctx"),
        "asserts": beat.get("asserts"),
        "supersedes": beat.get("supersedes"),
        "kind": beat.get("kind"),
        "arc": beat.get("arc"),
    }


def _from_special(
    special: tuple[str, str, str | None], namer: Namer, rng: random.Random
) -> dict[str, Any]:
    what, line, label = special
    return {
        "text": _fill(line, None, namer, rng),
        "special": what,
        "label": label,
    }


def _from_filler(namer: Namer, rng: random.Random) -> dict[str, Any]:
    pool = rng.choices(
        [FRAME_LOGISTICS, FRAME_UPDATE, FRAME_FOLLOWUP, FRAME_LONG],
        weights=[0.32, 0.36, 0.17, 0.15],
        k=1,
    )[0]
    text = _fill(rng.choice(pool), None, namer, rng)

    # A fifth of dictations run on into a second clause, which is what puts
    # the top of the length distribution where speech actually sits.
    if rng.random() < 0.42:
        tail = _fill(rng.choice(FRAME_TAIL), None, namer, rng)
        text = text.rstrip(".") + ", " + tail[0].lower() + tail[1:]
        if not text.endswith((".", "?", "!")):
            text += "."
    return {"text": text}


def _emit(
    index: int,
    when: datetime,
    app: str,
    context: str | None,
    item: dict[str, Any],
    rng: random.Random,
    noise: dict[str, Any],
    edit_prefs: list[dict[str, Any]],
    spec: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    text = item["text"]
    special = item.get("special")
    label = item.get("label")

    record: dict[str, Any] = {
        "external_id": f"d_{index:05d}",
        "occurred_at": when.isoformat(),
        "app": app,
    }

    # Roughly a quarter of dictations arrive with no window context, which is
    # what forces the tighter app-only sitting rule.
    if context and rng.random() < spec["grouping"]["with_context_hash"]:
        record["window_title"] = WINDOWS[context]

    record["formatted_text"] = text
    record["raw_asr"] = to_asr(text, rng, noise)
    record["asr_confidence"] = round(rng.uniform(0.82, 0.98), 2)
    record["duration_ms"] = max(900, int(len(text.split()) * rng.uniform(380, 620)))

    committed, operator = apply_edits(text, app, rng, edit_prefs)
    if committed is not None:
        record["committed_text"] = committed

    # Junk overrides: each reason needs a record that actually triggers it.
    if special == "retry":
        # The second take, seconds after the first. Nothing else marks it.
        pass
    if special == "junk":
        if label == "discarded_by_user":
            record["committed_text"] = ""
        elif label == "low_asr_confidence":
            record["asr_confidence"] = round(rng.uniform(0.18, 0.52), 2)
        elif label == "implausible_timing":
            record["duration_ms"] = rng.randint(9000, 14000)

    record["metadata"] = {"device": "macbook", "locale": "en-IN"}

    truth: dict[str, Any] | None = None
    if special or item.get("asserts") or operator:
        truth = {
            "external_id": record["external_id"],
            "expect": (
                "refused" if special == "sensitive"
                else "not_embedded" if special in ("junk", "retry")
                else "stored"
            ),
            "sensitive_category": label if special == "sensitive" else None,
            "ignore_reason": label if special in ("junk", "retry") else None,
            "asserts": item.get("asserts"),
            "supersedes": item.get("supersedes"),
            "memory_type": item.get("kind"),
            "style_operator": operator,
            "near_miss": special == "near_miss" or None,
        }
        truth = {k: v for k, v in truth.items() if v is not None}
    return record, truth


def _ground_truth(
    spec: dict[str, Any], records: list[dict], per_record: list[dict]
) -> dict[str, Any]:
    """What the corpus asserts, in LongMemEval's five ability categories."""
    arcs = spec["decision_arcs"]
    return {
        "corpus": {
            "records": len(records),
            "span_days": spec["meta"]["span_days"],
            "generated_from": "corpus/spec.yaml",
            "deterministic": True,
        },
        "abilities": {
            "information_extraction":
                "A claim stated once must be recoverable, including when its "
                "subject is only named earlier in the same episode.",
            "multi_session_reasoning":
                "Claims corroborated across apps and days must become one "
                "belief, not several.",
            "temporal_reasoning":
                "Questions scoped to a week or a month must filter by time "
                "before similarity, not after.",
            "knowledge_updates":
                "The final value of every arc below is what recall must "
                "return; earlier values stay answerable as history.",
            "abstention":
                "Questions with no support in the corpus must be refused, and "
                "an ambiguous person must be flagged rather than guessed.",
        },
        "decision_arcs": [
            {"id": arc["id"], "subject": arc["subject"], "final": arc["final"],
             "superseded": [step["value"] for step in arc["steps"][:-1]]}
            for arc in arcs
        ],
        "stated_preferences": [p["asserts"] for p in STATED_PREFERENCES],
        "edit_preferences": [
            {"operator": pref["operator"], "contexts": pref["contexts"]}
            for pref in spec["edit_preferences"]
        ],
        "ambiguous_entities": [
            {"surface": "Priya",
             "resolves_to": ["Priya Menon", "Priya Raghavan"],
             "expect": "flag ambiguity from week 7 rather than guessing"}
        ],
        "records": per_record,
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--records", type=int, default=500)
    parser.add_argument("--seed", type=int, default=20260615)
    parser.add_argument("--out", type=Path, default=OUT_CORPUS)
    args = parser.parse_args(argv)

    spec = yaml.safe_load(SPEC.read_text())
    records, truth = build(spec, args.records, args.seed)

    with args.out.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    OUT_TRUTH.write_text(json.dumps(truth, indent=2, ensure_ascii=False))

    refused = sum(1 for r in truth["records"] if r.get("expect") == "refused")
    junk = sum(1 for r in truth["records"] if r.get("expect") == "not_embedded")
    styled = sum(1 for r in truth["records"] if r.get("style_operator"))
    print(f"records      {len(records)} -> {args.out}")
    print(f"ground truth {len(truth['records'])} annotated -> {OUT_TRUTH}")
    print(f"  refused    {refused}")
    print(f"  junk       {junk}")
    print(f"  style edits{styled:>4}")
    print(f"seed         {args.seed} (regenerating gives the same file)")


if __name__ == "__main__":
    main()
