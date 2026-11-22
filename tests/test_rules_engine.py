"""Tests for evidence typing rules (LT-25)."""

from logtldr.models import Citation, Hypothesis, LLMResponse, LogLine
from logtldr.rules_engine import apply_rules


def _make_line(line_id: int, level: str, msg: str) -> LogLine:
    return LogLine(
        line_id=line_id,
        original_text=msg,
        redacted_text=msg,
        level=level,
        message=msg,
        source_format="plaintext",
    )


def _make_hyp(evidence: str, confidence: str, citations: list[Citation]) -> Hypothesis:
    return Hypothesis(
        cause="Test cause",
        evidence_type=evidence,
        confidence=confidence,
        citations=citations,
    )


def test_direct_evidence_in_catalog() -> None:
    """A direct claim with an ERROR line matching the catalog is kept."""
    # 'OOMKilled' is in the catalog
    lines = [
        _make_line(1, "INFO", "Starting up"),
        _make_line(2, "FATAL", "Container OOMKilled with exit code 137"),
    ]
    citations = [Citation(line_id=2, snippet="OOMKilled")]
    hyp = _make_hyp("direct", "high", citations)
    response = LLMResponse(status="ok", hypotheses=[hyp])

    result = apply_rules(response, lines)
    final_hyp = result.hypotheses[0]  # type: ignore

    # It should remain direct and high confidence
    assert final_hyp.evidence_type == "direct"
    assert final_hyp.confidence == "high"


def test_downgrade_temporal_to_medium() -> None:
    """Temporal evidence should never be high confidence."""
    lines = [_make_line(1, "WARN", "Something odd happened")]
    citations = [Citation(line_id=1, snippet="odd")]
    
    # LLM tries to give a temporal finding high confidence
    hyp = _make_hyp("temporal", "high", citations)
    response = LLMResponse(status="ok", hypotheses=[hyp])

    result = apply_rules(response, lines)
    final_hyp = result.hypotheses[0]  # type: ignore

    assert final_hyp.evidence_type == "temporal"
    assert final_hyp.confidence == "medium"


def test_unmatched_direct_becomes_temporal() -> None:
    """An invalid direct claim is downgraded to temporal."""
    # The line is an ERROR, but doesn't match any keyword in direct_causes.yaml
    lines = [
        _make_line(1, "ERROR", "Something entirely undocumented failed"),
    ]
    citations = [Citation(line_id=1, snippet="undocumented")]
    
    hyp = _make_hyp("direct", "high", citations)
    response = LLMResponse(status="ok", hypotheses=[hyp])

    result = apply_rules(response, lines)
    final_hyp = result.hypotheses[0]  # type: ignore

    # It should be downgraded to temporal, and confidence capped
    assert final_hyp.evidence_type == "temporal"
    assert final_hyp.confidence == "medium"


def test_direct_claim_on_info_line_becomes_temporal() -> None:
    """A direct claim citing only INFO lines is downgraded."""
    # 'OOMKilled' is in the catalog, but this is just an INFO line
    lines = [
        _make_line(1, "INFO", "Container OOMKilled with exit code 137"),
    ]
    citations = [Citation(line_id=1, snippet="OOMKilled")]
    
    hyp = _make_hyp("direct", "high", citations)
    response = LLMResponse(status="ok", hypotheses=[hyp])

    result = apply_rules(response, lines)
    final_hyp = result.hypotheses[0]  # type: ignore

    assert final_hyp.evidence_type == "temporal"
    assert final_hyp.confidence == "medium"