"""What the pipeline costs to run, measured rather than estimated.

Everything here comes from rows the system already writes: traces record a
latency, a token count and a cost for every model call, and Postgres knows
how large each table is. Nothing is instrumented specially for this script,
which is the point -- a cost figure produced by a separate measuring rig is
a figure for the rig.

One exception. No stage records retrieval on its own: the `recall` step
covers retrieval and generation together. Rather than report a number we do
not have, retrieval is timed here by running the searches with no model
call. Embeddings are local, so this costs nothing.

    python -m evaluation.systems
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select, text

from app.config import settings
from app.db.session import SessionLocal
from app.models.event import Event
from app.models.job import Job
from app.models.memory import Memory
from app.models.trace import Trace, TraceStep
from app.services import retrieval

ROOT = Path(__file__).resolve().parent.parent
QUESTIONS = ROOT / "evaluation" / "questions.yaml"
RESULTS = ROOT / "evaluation" / "results"


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    return float(ordered[min(len(ordered) - 1, int(len(ordered) * fraction))])


def _spread(values: list[float]) -> dict[str, float]:
    return {
        "n": len(values),
        "p50": round(_percentile(values, 0.50), 1),
        "p95": round(_percentile(values, 0.95), 1),
        "mean": round(statistics.fmean(values), 1) if values else 0.0,
    }


def latency(session, user_id) -> dict[str, Any]:
    """End to end, and per stage, from what the traces recorded."""
    out: dict[str, Any] = {}
    for kind in ("ingest", "query"):
        rows = list(
            session.scalars(
                select(Trace.total_latency_ms).where(
                    Trace.user_id == user_id,
                    Trace.kind == kind,
                    Trace.total_latency_ms.is_not(None),
                )
            )
        )
        out[f"{kind}_end_to_end_ms"] = _spread([float(row) for row in rows])

    stages: dict[str, list[float]] = {}
    for stage, value in session.execute(
        select(TraceStep.stage, TraceStep.latency_ms)
        .join(Trace, Trace.id == TraceStep.trace_id)
        .where(Trace.user_id == user_id, TraceStep.latency_ms.is_not(None))
    ):
        stages.setdefault(stage, []).append(float(value))
    out["by_stage_ms"] = {name: _spread(vals) for name, vals in sorted(stages.items())}
    return out


def model_usage(session, user_id) -> dict[str, Any]:
    """Calls, tokens and cost, split by what the work was."""
    out: dict[str, Any] = {}
    events = session.scalar(
        select(func.count()).select_from(Event).where(Event.user_id == user_id)
    ) or 0

    for kind in ("ingest", "query"):
        traces = list(
            session.scalars(select(Trace).where(Trace.user_id == user_id, Trace.kind == kind))
        )
        calls = session.scalar(
            select(func.count())
            .select_from(TraceStep)
            .join(Trace, Trace.id == TraceStep.trace_id)
            .where(Trace.user_id == user_id, Trace.kind == kind,
                   TraceStep.model.is_not(None))
        ) or 0
        out[kind] = {
            "traces": len(traces),
            "model_calls": calls,
            "input_tokens": sum(t.total_input_tokens for t in traces),
            "output_tokens": sum(t.total_output_tokens for t in traces),
            "cost_usd": round(sum(float(t.total_cost_usd or 0) for t in traces), 4),
        }

    ingest, query = out["ingest"], out["query"]
    out["per_event_ingested"] = {
        "events": events,
        "model_calls": round(ingest["model_calls"] / events, 3) if events else 0,
        "cost_usd": round(ingest["cost_usd"] / events, 6) if events else 0,
    }
    out["per_query"] = {
        "model_calls": round(query["model_calls"] / query["traces"], 2)
        if query["traces"] else 0,
        "cost_usd": round(query["cost_usd"] / query["traces"], 6)
        if query["traces"] else 0,
    }
    # The model is named on the steps, not guessed at, so a run that mixed
    # two of them says so rather than being reported under one.
    out["models_used"] = sorted(
        m for (m,) in session.execute(
            select(TraceStep.model).join(Trace, Trace.id == TraceStep.trace_id)
            .where(Trace.user_id == user_id, TraceStep.model.is_not(None)).distinct()
        )
    )
    return out


def storage(session, user_id) -> dict[str, Any]:
    """Bytes per table, and what that works out to per dictation."""
    events = session.scalar(
        select(func.count()).select_from(Event).where(Event.user_id == user_id)
    ) or 1
    rows = session.execute(text("""
        select c.relname, pg_total_relation_size(c.oid) as bytes
        from pg_class c join pg_namespace n on n.oid = c.relnamespace
        where n.nspname = 'public' and c.relkind = 'r'
        order by bytes desc
    """)).all()

    tables = {
        name: {"bytes": int(size), "bytes_per_event": round(int(size) / events, 1)}
        for name, size in rows if int(size) > 0
    }
    total = sum(t["bytes"] for t in tables.values())
    return {
        "events": events,
        "total_bytes": total,
        "total_bytes_per_event": round(total / events, 1),
        "by_table": tables,
    }


def throughput(session, user_id) -> dict[str, Any]:
    """How fast the pipeline actually consumed the corpus."""
    out: dict[str, Any] = {}
    for stage in ("episode_consolidate", "embed", "episode_assign"):
        rows = list(session.execute(
            select(Job.started_at, Job.finished_at).where(
                Job.user_id == user_id, Job.stage == stage, Job.status == "done",
                Job.started_at.is_not(None), Job.finished_at.is_not(None),
            )
        ))
        if not rows:
            continue
        started = min(r[0] for r in rows)
        finished = max(r[1] for r in rows)
        seconds = max((finished - started).total_seconds(), 0.001)
        busy = sum((r[1] - r[0]).total_seconds() for r in rows)
        out[stage] = {
            "jobs": len(rows),
            "wall_seconds": round(seconds, 1),
            "busy_seconds": round(busy, 1),
            # A rate over the span between the first and last job only means
            # something when the jobs filled that span. Four jobs scattered
            # across a long run give a number that describes the gaps.
            "jobs_per_second": (
                round(len(rows) / seconds, 3) if busy > seconds * 0.5 else None
            ),
        }

    events = session.scalar(
        select(func.count()).select_from(Event).where(Event.user_id == user_id)
    ) or 0
    consolidate = out.get("episode_consolidate")
    if consolidate and consolidate["wall_seconds"]:
        out["events_per_second_end_to_end"] = round(
            events / consolidate["wall_seconds"], 2
        )
    return out


def retrieval_only(session, user_id, questions: list[dict]) -> dict[str, Any]:
    """Time the search with no model in the way.

    Measured here rather than read from a trace because no stage records it
    alone. Local embeddings mean this is free to run.
    """
    now = datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    timings: list[float] = []
    for question in questions:
        started = time.perf_counter()
        retrieval.search(session, user_id, question["question"], now=now)
        timings.append((time.perf_counter() - started) * 1000)
    return _spread(timings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", default=None)
    parser.add_argument("--questions", default=str(QUESTIONS))
    args = parser.parse_args(argv)

    user_id = uuid.UUID(args.user_id) if args.user_id else settings.default_user_id
    questions = yaml.safe_load(Path(args.questions).read_text())["questions"]

    with SessionLocal() as session:
        report = {
            "measured_at": datetime.now(timezone.utc).isoformat(),
            "corpus": {
                "events": session.scalar(
                    select(func.count()).select_from(Event).where(Event.user_id == user_id)
                ),
                "memories": session.scalar(
                    select(func.count()).select_from(Memory).where(Memory.user_id == user_id)
                ),
            },
            "model_usage": model_usage(session, user_id),
            "latency": latency(session, user_id),
            "retrieval_only_ms": retrieval_only(session, user_id, questions),
            "throughput": throughput(session, user_id),
            "storage": storage(session, user_id),
        }

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    out = RESULTS / f"systems-{stamp}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "systems.json").write_text(json.dumps(report, indent=2, default=str))
    (out / "systems.md").write_text(_render(report))
    print(_render(report))
    print(f"wrote {out}/systems.json and systems.md")
    return 0


def _render(report: dict) -> str:
    usage = report["model_usage"]
    lat = report["latency"]
    store = report["storage"]
    lines = ["# Systems metrics", ""]
    lines.append(f"Model(s): {', '.join(usage['models_used']) or 'none recorded'}")
    lines.append(
        f"Corpus: {report['corpus']['events']} events, "
        f"{report['corpus']['memories']} memories"
    )

    lines += ["", "## Cost and calls", "",
              "| | ingest | query |", "|---|---|---|"]
    for label, key in [("traces", "traces"), ("model calls", "model_calls"),
                       ("input tokens", "input_tokens"),
                       ("output tokens", "output_tokens"), ("cost USD", "cost_usd")]:
        lines.append(f"| {label} | {usage['ingest'][key]} | {usage['query'][key]} |")
    lines += [
        "",
        f"Per event ingested: {usage['per_event_ingested']['model_calls']} model calls, "
        f"${usage['per_event_ingested']['cost_usd']}",
        f"Per query: {usage['per_query']['model_calls']} model calls, "
        f"${usage['per_query']['cost_usd']}",
    ]

    lines += ["", "## Latency (ms)", "", "| | n | p50 | p95 |", "|---|---|---|---|"]
    for label, key in [("ingest end to end", "ingest_end_to_end_ms"),
                       ("query end to end", "query_end_to_end_ms")]:
        s = lat[key]
        lines.append(f"| {label} | {s['n']} | {s['p50']} | {s['p95']} |")
    r = report["retrieval_only_ms"]
    lines.append(f"| retrieval only (no model) | {r['n']} | {r['p50']} | {r['p95']} |")
    for stage, s in lat["by_stage_ms"].items():
        lines.append(f"| stage: {stage} | {s['n']} | {s['p50']} | {s['p95']} |")

    lines += ["", "## Storage", "",
              f"{store['total_bytes'] / 1024:.0f} kB total, "
              f"{store['total_bytes_per_event']:.0f} bytes per event", "",
              "| table | bytes | per event |", "|---|---|---|"]
    for name, row in list(store["by_table"].items())[:8]:
        lines.append(f"| {name} | {row['bytes']} | {row['bytes_per_event']} |")

    lines += ["", "## Throughput", ""]
    for stage, row in report["throughput"].items():
        if not isinstance(row, dict):
            continue
        rate = (f"{row['jobs_per_second']}/s" if row["jobs_per_second"]
                else "rate not meaningful, the jobs did not fill the span")
        lines.append(
            f"- {stage}: {row['jobs']} jobs, {row['busy_seconds']}s of work "
            f"across {row['wall_seconds']}s ({rate})"
        )
    if "events_per_second_end_to_end" in report["throughput"]:
        lines.append(
            f"- end to end: {report['throughput']['events_per_second_end_to_end']} "
            f"events/second through consolidation"
        )
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
