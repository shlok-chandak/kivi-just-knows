"""Running the system with something deliberately taken away.

Two uses, one mechanism. A counterfactual replay removes one belief and asks
whether the answer depended on it -- which is the literal question "did
memory affect this result". An ablation removes a whole capability, like the
SQL filters, and asks whether the architecture earns its complexity.

Both are "the same request, minus something", so they are the same object.
Default is everything on, so a caller that ignores this changes nothing.

What is not here: a mode that answers with no sources at all. That ablation
exists to show the model inventing an answer, and a switch that turns off
grounding is not something production code should be able to reach. The
evaluation harness calls the model directly for it instead.
"""

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Ablation:
    """What to withhold from a run."""

    # Beliefs to pretend the system never formed. The counterfactual: if the
    # answer is unchanged without them, they were not what produced it.
    exclude_memory_ids: frozenset[uuid.UUID] = field(default_factory=frozenset)

    # Drop the SQL narrowing and let vector similarity decide alone. The
    # comparison that shows whether filtering before searching is worth it.
    skip_filters: bool = False

    # Stages to leave out entirely, named as in the trace.
    disable_stages: frozenset[str] = field(default_factory=frozenset)

    @property
    def active(self) -> bool:
        return bool(
            self.exclude_memory_ids or self.skip_filters or self.disable_stages
        )

    def allows(self, stage: str) -> bool:
        return stage not in self.disable_stages

    def as_dict(self) -> dict:
        return {
            "exclude_memory_ids": sorted(str(i) for i in self.exclude_memory_ids),
            "skip_filters": self.skip_filters,
            "disable_stages": sorted(self.disable_stages),
        }

    @classmethod
    def parse(cls, payload: dict | None) -> "Ablation":
        """Build one from request JSON, tolerating missing keys."""
        if not payload:
            return cls()
        return cls(
            exclude_memory_ids=frozenset(
                uuid.UUID(str(value))
                for value in payload.get("exclude_memory_ids") or []
            ),
            skip_filters=bool(payload.get("skip_filters")),
            disable_stages=frozenset(payload.get("disable_stages") or []),
        )


NONE = Ablation()
