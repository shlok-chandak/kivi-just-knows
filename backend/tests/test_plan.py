"""Planning: which tools run for a parsed request.

Rules, so every case here is deterministic and none of them calls a model.
"""

from app.schemas.query import QuerySpec
from app.services.plan import MAX_STEPS, plan


def spec(**kwargs) -> QuerySpec:
    return QuerySpec(intent=kwargs.pop("intent", "recall"),
                     topic=kwargs.pop("topic", "pricing"), **kwargs)


def test_a_question_is_answered_from_history():
    assert plan(spec()).tools == ["recall"]


def test_finding_a_dictation_is_one_step():
    assert plan(spec(intent="find_dictation")).tools == ["find_dictation"]


def test_finding_then_polishing_is_the_briefs_chained_example():
    """"Find the 5 PM dictation and polish it" is two tools, not two requests."""
    chained = plan(spec(intent="find_dictation", style_hint="for the meeting"))
    assert chained.tools == ["find_dictation", "restyle"]
    assert chained.steps[1].takes_input_from == "find_dictation"


def test_a_plain_find_does_not_restyle():
    assert plan(spec(intent="find_dictation", style_hint=None)).tools == [
        "find_dictation"
    ]


def test_drafting_retrieves_before_it_writes():
    """Composing without retrieving first is writing fiction."""
    drafted = plan(spec(intent="draft", artifact="message"))
    assert drafted.tools == ["recall", "draft"]
    assert drafted.steps[1].takes_input_from == "recall"


def test_restyling_supplied_text_needs_no_retrieval():
    assert plan(spec(intent="restyle")).tools == ["restyle"]


def test_memory_control_is_a_single_step_either_way():
    assert plan(spec(intent="memory_control")).tools == ["memory_control"]
    assert plan(spec(intent="memory_control", forget=True)).tools == [
        "memory_control"
    ]


def test_forgetting_is_stated_in_the_rationale():
    """The trace has to distinguish showing from deleting."""
    assert "delete" in plan(spec(intent="memory_control", forget=True)).rationale
    assert "show" in plan(spec(intent="memory_control")).rationale


def test_no_plan_exceeds_the_cap():
    for intent in ("recall", "find_dictation", "restyle", "draft", "memory_control"):
        made = plan(spec(intent=intent, style_hint="casual", forget=True))
        assert len(made.steps) <= MAX_STEPS
