#!/usr/bin/env python
"""Recompute stored costs from the token counts already recorded.

Cost is written onto each step at the moment of the call, using whatever
the pricing table knew then. A model missing from that table falls back to
the most expensive known rate, so a run made before its rate was added
carries a figure roughly double the real one -- and every report drawn
from those rows inherits it.

The tokens themselves are recorded, so nothing was lost: the cost is
derivable and this recomputes it in place. Run it after adding or
correcting a rate.

    python -m scripts.recost_traces --dry-run
    python -m scripts.recost_traces
"""

from __future__ import annotations

import argparse
from collections import Counter

from sqlalchemy import select

from app.db.session import SessionLocal
from app.llm.pricing import cost_usd, rate_for
from app.models.trace import Trace, TraceStep


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="report the change, write nothing"
    )
    args = parser.parse_args(argv)

    changed = 0
    still_unknown: Counter[str] = Counter()
    before = after = 0.0

    with SessionLocal() as session:
        steps = list(
            session.scalars(select(TraceStep).where(TraceStep.model.is_not(None)))
        )

        for step in steps:
            _, guessed = rate_for(step.model)
            if guessed:
                # Still no published rate, so the fallback is still the
                # honest answer for this one. Counted, not silently kept.
                still_unknown[step.model] += 1

            recomputed = cost_usd(
                step.model, step.input_tokens or 0, step.output_tokens or 0
            )
            old = float(step.cost_usd or 0.0)
            before += old
            after += recomputed

            if abs(recomputed - old) > 1e-12:
                changed += 1
                if not args.dry_run:
                    step.cost_usd = recomputed

        # Totals are sums of their steps, so they move with them rather
        # than being recomputed by a second rule that could disagree.
        if not args.dry_run:
            for trace in session.scalars(select(Trace)):
                trace.total_cost_usd = sum(
                    float(step.cost_usd or 0.0)
                    for step in session.scalars(
                        select(TraceStep).where(TraceStep.trace_id == trace.id)
                    )
                )
            session.commit()

    print(f"steps with a model : {len(steps)}")
    print(f"costs changed      : {changed}")
    print(f"total before       : ${before:.4f}")
    print(f"total after        : ${after:.4f}")
    if before:
        print(f"overstated by      : {before / after:.2f}x" if after else "")
    for model, count in still_unknown.items():
        print(f"still unpriced     : {model} ({count} calls, upper bound)")
    if args.dry_run:
        print("\ndry run, nothing written")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
