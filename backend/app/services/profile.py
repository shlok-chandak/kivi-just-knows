"""The small always-on memory, sent with every prompt.

Mostly who the user is and how they like things done. A preference only
helps if it is already there while an answer is being written -- retrieving
it afterwards is too late, because the answer has already been phrased.

A few lines of what they are working on come too, so a short question does
not arrive with no footing at all. Deliberately a few: current work is
retrievable on demand and changes weekly, while style is stable and cheap.
Filling this with project detail would spend a permanent budget on something
a search answers better.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.models.job import Job
from app.models.memory import Memory
from app.models.profile import STATES, ProfileEntry
from app.services import ranking

# The queue stage that rebuilds this, and its subject. One subject so repeated
# requests coalesce into a single job.
REFRESH_STAGE = "profile_refresh"
REFRESH_SUBJECT = "profile"

# How often it may rebuild. Slow on purpose: this is the one thing sent with
# every question, so it should be stable enough to read and correct rather
# than shifting under the person after each thing they dictate.
REFRESH_INTERVAL = timedelta(days=1)

# Hard cap, not a guideline.
BUDGET_TOKENS = 1500

# Style comes first and takes what it needs; work context gets what is left,
# up to this. Without a separate cap a busy week would evict the preferences.
MAX_WORK_LINES = 5

# Rough estimate. Not a tokeniser -- it only has to be close and never under.
CHARS_PER_TOKEN = 3.5

STYLE_TYPES = ("preference",)
WORK_TYPES = ("commitment", "decision")

# Facts are left out deliberately. "They work at Haulr" belongs here and "the
# billing rewrite is blocked" does not, and nothing in a fact's shape tells
# the two apart -- a rule guessing from digits and repetition filled this
# section with project status.


@dataclass
class Entry:
    memory: Memory
    score: float
    tokens: int


@dataclass
class Profile:
    """What is resident, and what was dropped to make it fit."""

    style: list[Entry] = field(default_factory=list)
    work: list[Entry] = field(default_factory=list)
    evicted: list[Entry] = field(default_factory=list)
    tokens: int = 0

    def rendered(self) -> str:
        """The profile as it reaches a prompt."""
        blocks: list[str] = []
        if self.style:
            blocks.append(
                "How they like things done:\n"
                + "\n".join(f"- {entry.memory.content}" for entry in self.style)
            )
        if self.work:
            blocks.append(
                "Currently working on:\n"
                + "\n".join(f"- {entry.memory.content}" for entry in self.work)
            )
        return "\n\n".join(blocks)


def estimate_tokens(text: str) -> int:
    return max(1, round(len(text) / CHARS_PER_TOKEN)) + 2


def _select(
    session: Session,
    user_id: uuid.UUID,
    *,
    now: datetime,
    budget_tokens: int,
    hidden: set[uuid.UUID],
) -> Profile:
    """Rank the candidates and cut them to the budget.

    Superseded and candidate memories are left out: a replaced belief
    repeated in every prompt is worse than not having it, and an inference
    heard once has not earned a permanent seat.
    """
    live = [
        memory
        for memory in session.scalars(
            select(Memory).where(
                Memory.user_id == user_id,
                Memory.status == "active",
            )
        )
        if memory.id not in hidden
    ]

    def entries_for(memories: list[Memory]) -> list[Entry]:
        made = [
            Entry(
                memory=memory,
                score=ranking.score(memory, now),
                tokens=estimate_tokens(memory.content),
            )
            for memory in memories
            if not ranking.is_expired(memory, now)
        ]
        made.sort(key=lambda entry: entry.score, reverse=True)
        return made

    style = entries_for([m for m in live if m.type in STYLE_TYPES])
    # Work is ordered by when it was last said, not by the durability score
    # used everywhere else. A decision holds its confidence until overturned,
    # which is right for answering and wrong here -- it put a two-month-old
    # decision under "currently working on". The cap keeps the section
    # current: new work pushes old work out.
    work = entries_for([m for m in live if m.type in WORK_TYPES])
    work.sort(key=lambda entry: entry.memory.last_reinforced_at, reverse=True)

    profile = Profile()
    used = 0

    for entry in style:
        # Keep going past one that does not fit, so a single long line cannot
        # evict every short one behind it.
        if used + entry.tokens <= budget_tokens:
            profile.style.append(entry)
            used += entry.tokens
        else:
            profile.evicted.append(entry)

    for entry in work:
        if (
            len(profile.work) < MAX_WORK_LINES
            and used + entry.tokens <= budget_tokens
        ):
            profile.work.append(entry)
            used += entry.tokens
        else:
            profile.evicted.append(entry)

    profile.tokens = used
    return profile


def refresh(
    session: Session,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
    budget_tokens: int = BUDGET_TOKENS,
) -> Profile:
    """Recompute what is resident, leaving the person's own edits alone.

    Only `auto` rows are replaced. A pinned entry stays whatever the ranking
    thinks, and a hidden one stays out -- otherwise an edit would last until
    the next consolidation and no further, which is not an edit.
    """
    now = now or datetime.now(timezone.utc)

    kept = list(
        session.scalars(
            select(ProfileEntry).where(
                ProfileEntry.user_id == user_id,
                ProfileEntry.state != "auto",
            )
        )
    )
    hidden = {entry.memory_id for entry in kept if entry.state == "hidden"}
    pinned = {entry.memory_id for entry in kept if entry.state == "pinned"}

    session.execute(
        delete(ProfileEntry).where(
            ProfileEntry.user_id == user_id,
            ProfileEntry.state == "auto",
        )
    )

    chosen = _select(
        session, user_id, now=now, budget_tokens=budget_tokens, hidden=hidden
    )

    for section, entries in (("style", chosen.style), ("work", chosen.work)):
        for position, entry in enumerate(entries):
            # A pinned memory already has a row; re-adding it would collide
            # with the one-row-per-memory index.
            if entry.memory.id in pinned:
                continue
            session.add(
                ProfileEntry(
                    id=uuid.uuid4(),
                    user_id=user_id,
                    memory_id=entry.memory.id,
                    section=section,
                    state="auto",
                    position=position,
                    score=entry.score,
                )
            )

    session.flush()
    return load(session, user_id, now=now)


def load(
    session: Session,
    user_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> Profile:
    """Read the stored profile. No ranking, no model, just rows."""
    now = now or datetime.now(timezone.utc)

    rows = session.execute(
        select(ProfileEntry, Memory)
        .join(Memory, Memory.id == ProfileEntry.memory_id)
        .where(
            ProfileEntry.user_id == user_id,
            ProfileEntry.state != "hidden",
        )
        .order_by(ProfileEntry.section, ProfileEntry.position)
    ).all()

    profile = Profile()
    for row, memory in rows:
        entry = Entry(
            memory=memory,
            score=row.score or 0.0,
            tokens=estimate_tokens(memory.content),
        )
        # A pinned entry is shown even if the memory was later superseded --
        # the person asked for it. Everything else follows the usual rule.
        if memory.status != "active" and row.state != "pinned":
            continue
        (profile.style if row.section == "style" else profile.work).append(entry)
        profile.tokens += entry.tokens

    return profile


def set_state(
    session: Session, user_id: uuid.UUID, memory_id: uuid.UUID, state: str
) -> ProfileEntry:
    """Pin, hide, or hand an entry back to the ranking."""
    if state not in STATES:
        raise ValueError(f"unknown state: {state}")

    entry = session.scalars(
        select(ProfileEntry).where(
            ProfileEntry.user_id == user_id,
            ProfileEntry.memory_id == memory_id,
        )
    ).first()

    if entry is None:
        memory = session.get(Memory, memory_id)
        if memory is None or memory.user_id != user_id:
            raise ValueError(f"no such memory: {memory_id}")
        entry = ProfileEntry(
            id=uuid.uuid4(),
            user_id=user_id,
            memory_id=memory_id,
            section="work" if memory.type in WORK_TYPES else "style",
            state=state,
            position=0,
        )
        session.add(entry)
    else:
        entry.state = state

    session.flush()
    return entry


def next_refresh_due(
    session: Session, user_id: uuid.UUID, *, now: datetime | None = None
) -> datetime:
    """When a rebuild may next run.

    A day after the last one finished, or now if there has not been one. The
    caller passes this as the job's run_after, so the wait costs nothing --
    the job sits in the queue rather than a timer running somewhere.
    """
    now = now or datetime.now(timezone.utc)
    last = session.scalar(
        select(func.max(Job.finished_at)).where(
            Job.user_id == user_id,
            Job.stage == REFRESH_STAGE,
            Job.status == "done",
        )
    )
    if last is None:
        return now
    return max(now, last + REFRESH_INTERVAL)
