"""Tests for the renderer (LT-29)."""

from logtldr.models import FinalSummary, Hypothesis
from logtldr.renderer import format_verdict


def _make_summary(cause: str, confidence: str = "high", status: str = "ok") -> FinalSummary:
    hyp = Hypothesis(
        cause=cause,
        evidence_type="direct",
        confidence=confidence,
        citations=[],
    )
    return FinalSummary(status=status, hypotheses=[hyp])


def test_verdict_line_format() -> None:
    """The verdict must follow the exact 'Evidence points to...' format."""
    summary = _make_summary("a database connection failure", "medium")
    
    verdict = format_verdict(summary)
    
    assert verdict == "Evidence points to a database connection failure, confidence: medium"
    assert "root cause is" not in verdict.lower()
    assert "medium" in verdict


def test_verdict_scrubs_root_cause_phrase() -> None:
    """If the LLM says 'root cause is', the renderer must scrub it."""
    summary = _make_summary("The root cause is a disk full error", "high")
    
    verdict = format_verdict(summary)
    
    assert "root cause is" not in verdict.lower()
    # It should gracefully handle the scrubbing without breaking the sentence
    assert verdict == "Evidence points to a disk full error, confidence: high"


def test_verdict_healthy() -> None:
    """A healthy log should have a clear, non-technical verdict."""
    summary = FinalSummary(status="nothing_unusual")
    assert format_verdict(summary) == "Nothing unusual detected in the logs."


def test_verdict_not_enough_signal() -> None:
    """A refused log should have a clear explanation."""
    summary = FinalSummary(status="not_enough_signal")
    assert format_verdict(summary) == "Not enough signal to determine a cause."