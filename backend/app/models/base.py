"""Declarative base and the column mixin shared by every table."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Root of the ORM hierarchy. Alembic autogenerates from `Base.metadata`."""


class UserOwnedMixin:
    """Identity and ownership columns carried by every table.

    The build is single-user, but every query filters on `user_id` so that
    supporting multiple users stays a deployment concern, not a schema rewrite.
    """

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4
    )

    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
