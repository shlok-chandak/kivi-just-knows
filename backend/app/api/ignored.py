"""What was heard and deliberately not kept.

A memory system is judged as much by what it declines as by what it stores,
and a refusal nobody can see is indistinguishable from a bug. This lists
every decision not to remember, with the rule that made it.

Sensitive refusals show when and where but never what. The content is the
thing being refused, so storing it to display it later would undo the
refusal. A visible gap is the honest version.
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.db.session import get_db
from app.models.rejected import RejectedCandidate

router = APIRouter(prefix="/ignored", tags=["ignored"])

# Rules where showing the candidate would defeat the refusal.
WITHHELD = ("sensitive_category", "sensitive_on_review")


@router.get("")
def listing(
    db: Session = Depends(get_db),
    rule: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> dict:
    """Everything declined, newest first, with a count per rule."""
    user_id = settings.default_user_id

    counts = dict(
        db.execute(
            select(RejectedCandidate.rejection_rule, func.count())
            .where(RejectedCandidate.user_id == user_id)
            .group_by(RejectedCandidate.rejection_rule)
        ).all()
    )

    query = select(RejectedCandidate).where(RejectedCandidate.user_id == user_id)
    if rule:
        query = query.where(RejectedCandidate.rejection_rule == rule)

    rows = list(
        db.scalars(query.order_by(RejectedCandidate.at.desc()).limit(limit))
    )

    return {
        "by_rule": counts,
        "total": sum(counts.values()),
        "ignored": [_row(row) for row in rows],
    }


def _row(row: RejectedCandidate) -> dict:
    withheld = row.rejection_rule in WITHHELD
    return {
        "id": str(row.id),
        "rule": row.rejection_rule,
        "rationale": row.rationale,
        "at": row.at.isoformat() if row.at else None,
        "app": row.app,
        "occurred_at": row.occurred_at.isoformat() if row.occurred_at else None,
        "event_id": str(row.event_id) if row.event_id else None,
        "episode_id": str(row.episode_id) if row.episode_id else None,
        "candidate": None if withheld else row.candidate,
        "withheld": withheld,
    }
