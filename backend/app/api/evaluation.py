"""Reading back what was measured.

These are recorded runs, not live ones. Nothing here evaluates anything --
it reads the files the harness wrote, so a result shown on screen is the
same artefact that went into the report, with no second code path that
could round differently or quietly re-score.

Running an evaluation from here would be the wrong shape anyway: a full run
is 240 model calls against a daily quota, which is a decision someone makes
deliberately at a terminal, not something a page fires on a click. Re-asking
a single question live is the useful interactive version, and that already
exists at /stream/ask.
"""

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

router = APIRouter(prefix="/evaluation", tags=["evaluation"])

RESULTS = Path(__file__).resolve().parent.parent.parent / "evaluation" / "results"

# A run is named by the directory the harness created. Constrained rather
# than trusted: this value arrives from a URL and is joined onto a path.
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# Which file names which kind of run. Ordered, so a directory holding more
# than one is reported as the first that matches rather than at random.
KINDS: tuple[tuple[str, str], ...] = (
    ("results.json", "questions"),
    ("tools.json", "tools"),
    ("systems.json", "systems"),
)


def _payload(directory: Path) -> tuple[str, Path] | None:
    """The kind of run this directory holds, and the file holding it."""
    for filename, kind in KINDS:
        candidate = directory / filename
        if candidate.is_file():
            return kind, candidate
    return None


def _written_at(path: Path) -> str:
    """When the file was written, for a run that recorded no timestamp."""
    return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat()


def _headline(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """The one line that belongs on a list row.

    Deliberately different per kind. A question run is judged on accuracy
    per arm, a tool run on how many safety properties held, a systems run
    on what it cost -- and flattening those into one shared number would
    describe none of them.
    """
    if kind == "questions":
        overall = (data.get("summary") or {}).get("overall") or {}
        return {
            "arms": {
                arm: {
                    "correct": scores.get("correct"),
                    "n": scores.get("n"),
                    "accuracy": scores.get("accuracy"),
                }
                for arm, scores in overall.items()
            },
            "questions": len(data.get("questions") or []),
        }

    if kind == "tools":
        return {
            "correct": data.get("correct"),
            "total": data.get("total"),
            "sections": len(data.get("sections") or []),
        }

    usage = data.get("model_usage") or {}
    return {
        "events": (data.get("corpus") or {}).get("events"),
        "memories": (data.get("corpus") or {}).get("memories"),
        "cost_per_event_usd": (usage.get("per_event_ingested") or {}).get("cost_usd"),
        "models": usage.get("models_used") or [],
    }


@router.get("/runs")
def runs(limit: int = Query(default=50, ge=1, le=200)) -> dict:
    """Recorded runs, newest first.

    An empty list is a normal answer, not an error. The results directory
    is gitignored, so a fresh clone has no runs at all and the screen has
    to say so rather than break.
    """
    if not RESULTS.is_dir():
        return {"runs": [], "total": 0, "note": "no runs recorded yet"}

    # By when the run happened, not by name. The directories are named for
    # their kind as well as their timestamp -- "tools-..." and "20260910-..."
    # -- so sorting the names reverse-alphabetically ordered them by kind
    # and called it newest first.
    directories = sorted(
        (path for path in RESULTS.iterdir() if path.is_dir()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    found: list[dict[str, Any]] = []
    for directory in directories:

        held = _payload(directory)
        if held is None:
            # An empty or half-written directory. Skipped rather than
            # reported as broken: an interrupted run is not a failure
            # anyone needs to see on this screen.
            continue

        kind, path = held
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            found.append({"id": directory.name, "kind": kind, "unreadable": True})
            continue

        found.append(
            {
                "id": directory.name,
                "kind": kind,
                # The run's own timestamp when it recorded one; otherwise
                # when the file was written, so a row always has a date.
                "at": (
                    data.get("measured_at")
                    or data.get("started_at")
                    or _written_at(path)
                ),
                "headline": _headline(kind, data),
            }
        )
        if len(found) >= limit:
            break

    return {"runs": found, "total": len(found)}


@router.get("/runs/{run_id}")
def run(run_id: str) -> dict:
    """One recorded run, in full, exactly as the harness wrote it."""
    if not _RUN_ID.match(run_id):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "bad run id")

    directory = (RESULTS / run_id).resolve()
    # Belt and braces over the pattern above: whatever the name parsed as,
    # what it resolves to has to sit inside the results directory.
    if not directory.is_dir() or RESULTS.resolve() not in directory.parents:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such run.")

    held = _payload(directory)
    if held is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That run recorded nothing.")

    kind, path = held
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"Run is unreadable: {exc}"
        ) from exc

    return {
        "id": run_id,
        "kind": kind,
        "headline": _headline(kind, data),
        "report_md": _report(directory, path),
        "data": data,
    }


def _report(directory: Path, payload: Path) -> str | None:
    """The human-readable write-up beside the data, if the harness made one.

    The name is not derivable from the payload's: the question harness
    writes results.json next to report.md, while the systems one writes a
    matching pair. Both are tried rather than assumed.
    """
    for name in (f"{payload.stem}.md", "report.md"):
        candidate = directory / name
        if candidate.is_file():
            return candidate.read_text()
    return None
