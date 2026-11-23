"""Tests for the renderer (LT-29, LT-30)."""

from logtldr.models import FinalSummary, Hypothesis
from logtldr.renderer import format_timeline, format_verdict


def _make_summary(cause: str, confidence: str = "high", status: str = "ok") -> FinalSummary:
    hyp = Hypothesis(
        cause=cause,
        evidence_type="direct",
        confidence=confidence,
        citations=[],
    )
    return FinalSummary(status=status, hypotheses=[hyp])


# ── LT-29 verdict tests ──────────────────────────────────────────────


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


# ── LT-30 timeline tests ─────────────────────────────────────────────


def test_timeline_anomaly_first() -> None:
    """Timeline starts at the first anomaly, events in chronological order."""
    timeline = [
        {
            "time": "2024-01-15T10:00:05Z",
            "event": "warning",
            "citations": [{"line_id": 5, "snippet": "WARN disk usage 90%"}],
        },
        {
            "time": "2024-01-15T10:00:12Z",
            "event": "spike",
            "citations": [{"line_id": 12, "snippet": "ERROR write latency spike"}],
        },
        {
            "time": "2024-01-15T10:00:45Z",
            "event": "crash",
            "citations": [{"line_id": 45, "snippet": "FATAL disk full, shutting down"}],
        },
    ]
    summary = FinalSummary(status="ok", timeline=timeline)

    result = format_timeline(summary)

    assert result == "line 5: warning → line 12: spike → line 45: crash"


def test_timeline_preserves_chronological_order() -> None:
    """Even if events arrive out of order, they render chronologically."""
    timeline = [
        {
            "time": "2024-01-15T10:00:45Z",
            "event": "crash",
            "citations": [{"line_id": 45, "snippet": "FATAL crash"}],
        },
        {
            "time": "2024-01-15T10:00:05Z",
            "event": "warning",
            "citations": [{"line_id": 5, "snippet": "WARN disk"}],
        },
    ]
    summary = FinalSummary(status="ok", timeline=timeline)

    result = format_timeline(summary)

    # Must be sorted by line_id, not by insertion order
    assert result == "line 5: warning → line 45: crash"


def test_timeline_uses_original_line_numbers() -> None:
    """Line numbers in the timeline are the original line numbers."""
    timeline = [
        {
            "time": "2024-01-15T10:00:00Z",
            "event": "OOM killed",
            "citations": [{"line_id": 1337, "snippet": "OOM killer invoked"}],
        },
    ]
    summary = FinalSummary(status="ok", timeline=timeline)

    result = format_timeline(summary)

    assert "line 1337:" in result


def test_timeline_empty_when_missing() -> None:
    """No timeline field → empty string."""
    summary = FinalSummary(status="ok")
    assert format_timeline(summary) == ""


def test_timeline_empty_list() -> None:
    """Empty timeline list → empty string."""
    summary = FinalSummary(status="ok", timeline=[])
    assert format_timeline(summary) == ""