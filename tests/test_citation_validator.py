"""Tests for citation validation (LT-18)."""

from logtldr.citation_validator import validate_citations
from logtldr.models import Citation, Hypothesis, LLMResponse, LogLine


def _make_line(line_id: int, text: str) -> LogLine:
    return LogLine(
        line_id=line_id,
        original_text=text,
        redacted_text=text,
        level="ERROR",
        message=text,
        source_format="plaintext",
    )


def test_valid_citation() -> None:
    """A perfectly matching citation should pass validation."""
    lines = [
        _make_line(1, "INFO starting"),
        _make_line(2, "ERROR disk full"),
    ]
    
    citation = Citation(line_id=2, snippet="disk full")
    hyp = Hypothesis(cause="Disk full", evidence_type="direct", confidence="high", citations=[citation])
    response = LLMResponse(status="ok", hypotheses=[hyp])

    result = validate_citations(response, lines)

    assert result.status == "ok"
    assert len(result.hypotheses) == 1
    assert result.hypotheses[0].citations[0].citation_valid is True


def test_invalid_citation() -> None:
    """If the line_id doesn't exist or snippet doesn't match, drop the hypothesis."""
    lines = [_make_line(1, "INFO starting")]

    # 1. Bad line_id
    bad_id_cit = Citation(line_id=99, snippet="starting")
    # 2. Bad snippet (doesn't appear in line 1)
    bad_snip_cit = Citation(line_id=1, snippet="disk full")
    
    hyp1 = Hypothesis(cause="Bad ID", evidence_type="direct", confidence="high", citations=[bad_id_cit])
    hyp2 = Hypothesis(cause="Bad Snip", evidence_type="direct", confidence="high", citations=[bad_snip_cit])
    response = LLMResponse(status="ok", hypotheses=[hyp1, hyp2])

    result = validate_citations(response, lines)

    # Both hypotheses should be dropped
    assert result.status == "not_enough_signal"
    assert result.hypotheses is None


def test_drop_invalid_promote_next() -> None:
    """If the top hypothesis is invalid, the next one should be promoted."""
    lines = [_make_line(1, "ERROR disk full")]
    
    # Top hypothesis has a hallucinated citation
    bad_cit = Citation(line_id=1, snippet="I made this up")
    top_hyp = Hypothesis(cause="Wrong", evidence_type="direct", confidence="high", citations=[bad_cit])
    
    # Next hypothesis is valid
    good_cit = Citation(line_id=1, snippet="disk full")
    next_hyp = Hypothesis(cause="Right", evidence_type="direct", confidence="high", citations=[good_cit])
    
    response = LLMResponse(status="ok", hypotheses=[top_hyp, next_hyp])

    result = validate_citations(response, lines)

    assert result.status == "ok"
    assert len(result.hypotheses) == 1
    # The first one is dropped, so the second one becomes the top (index 0)
    assert result.hypotheses[0].cause == "Right"