"""Rules engine (AD-5)."""

from logtldr.models import FinalSummary, LLMResponse


# TODO(LT-RULES): Replace with evidence typing, confidence capping, and unknowns.
def apply_rules(response: LLMResponse) -> FinalSummary:
    """Apply rules. Stub just maps LLMResponse to FinalSummary."""
    return FinalSummary(
        status=response.status,
        verdict=response.verdict,
        hypotheses=response.hypotheses,
        timeline=response.timeline,
        unknowns=[],
        check_next=response.check_next,
    )