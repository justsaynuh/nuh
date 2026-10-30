"""Shared by the LLM layer tests (test_llm.py, test_llm_transcript.py)."""

from nuh.bash_types import ClassifyResult, StageResult
from nuh import taxonomy


# -- shared helper --


def _make_default_result():
    """Build a default ClassifyResult for unknown command tests."""
    sr = StageResult(
        tokens=["foobar"],
        action_type=taxonomy.UNKNOWN,
        default_policy=taxonomy.ASK,
        decision=taxonomy.ASK,
        reason="unknown command",
    )
    return ClassifyResult(command="foobar", stages=[sr], final_decision=taxonomy.ASK, reason="unknown command")
