"""What the always-on profile currently holds.

Stored rather than recomputed per question. A profile that exists only for
the instant it is used cannot be shown to anyone or edited by them, and
being able to see and correct what a system believes about you is most of
what makes it trustworthy.

Refresh recomputes the `auto` rows and leaves the rest alone. That is the
whole point of the state column: without it, removing an entry would last
until the next consolidation and no longer.
"""

import uuid

from sqlalchemy import ForeignKey, Index, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UserOwnedMixin

# Where an entry sits. Style is who the person is, work is what they are on
# now, and the two are budgeted separately so a busy week cannot evict a
# standing preference.
SECTIONS = ("style", "work")

# auto   - chosen by ranking; refresh may replace it
# pinned - the person put it here; refresh never removes it
# hidden - the person took it out; refresh never puts it back
STATES = ("auto", "pinned", "hidden")


class ProfileEntry(UserOwnedMixin, Base):
    __tablename__ = "profile_entries"

    memory_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("memories.id", ondelete="CASCADE"), nullable=False
    )
    section: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="auto")

    # Order within the section, lowest first. Stored so what the model was
    # shown can be reconstructed later, rather than depending on a ranking
    # function that has since changed.
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # The score at the time it was placed. Kept for the same reason.
    score: Mapped[float | None] = mapped_column(nullable=True)

    __table_args__ = (
        # One row per memory. A memory cannot be both pinned and hidden, and
        # cannot appear in both sections.
        Index(
            "uq_profile_entry_memory",
            "user_id",
            "memory_id",
            unique=True,
        ),
        Index("ix_profile_entries_user_section", "user_id", "section", "position"),
    )
