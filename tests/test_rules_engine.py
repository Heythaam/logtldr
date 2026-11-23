"""Tests for evidence typing and confidence capping (LT-25 & LT-26)."""

from datetime import datetime, timedelta

from logtldr.models import Citation, Hypothesis, LLMResponse, LogLine
from logtldr.rules_engine import apply_rules, is_generic_advice


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


# --- LT-25 Tests ---

def test_direct_evidence_in_catalog() -> None:
    """A direct claim with an ERROR line matching the catalog is kept."""
    lines = [
        _make_line(1, "INFO", "Starting up"),
        _make_line(2, "FATAL", "Container OOMKilled with exit code 137"),
    ]
    citations = [Citation(line_id=2, snippet="OOMKilled")]
    hyp = _make_hyp("direct", "high", citations)
    response = LLMResponse(status="ok", hypotheses=[hyp])

    result = apply_rules(response, lines)
    final_hyp = result.hypotheses[0]  # type: ignore

    assert final_hyp.evidence_type == "direct"
    assert final_hyp.confidence == "high"


def test_unmatched_direct_becomes_temporal() -> None:
    """An invalid direct claim is downgraded to temporal."""
    lines = [_make_line(1, "ERROR", "Something entirely undocumented failed")]
    citations = [Citation(line_id=1, snippet="undocumented")]
    hyp = _make_hyp("direct", "high", citations)
    response = LLMResponse(status="ok", hypotheses=[hyp])

    result = apply_rules(response, lines)
    final_hyp = result.hypotheses[0]  # type: ignore

    assert final_hyp.evidence_type == "temporal"
    assert final_hyp.confidence == "medium"


def test_direct_claim_on_info_line_becomes_temporal() -> None:
    """A direct claim citing only INFO lines is downgraded."""
    lines = [_make_line(1, "INFO", "Container OOMKilled with exit code 137")]
    citations = [Citation(line_id=1, snippet="OOMKilled")]
    hyp = _make_hyp("direct", "high", citations)
    response = LLMResponse(status="ok", hypotheses=[hyp])

    result = apply_rules(response, lines)
    final_hyp = result.hypotheses[0]  # type: ignore

    assert final_hyp.evidence_type == "temporal"
    assert final_hyp.confidence == "medium"


# --- LT-26 Tests ---

def test_temporal_cap_medium() -> None:
    """Temporal evidence proposed as 'high' is capped to 'medium'."""
    lines = [_make_line(1, "WARN", "Something odd happened")]
    citations = [Citation(line_id=1, snippet="odd")]
    hyp = _make_hyp("temporal", "high", citations)
    response = LLMResponse(status="ok", hypotheses=[hyp])

    result = apply_rules(response, lines)
    final_hyp = result.hypotheses[0]  # type: ignore

    assert final_hyp.evidence_type == "temporal"
    assert final_hyp.confidence == "medium"


def test_temporal_never_upgrades_low() -> None:
    """If the LLM proposes 'low' for temporal, it should NOT be upgraded to 'medium'."""
    lines = [_make_line(1, "WARN", "Something odd happened")]
    citations = [Citation(line_id=1, snippet="odd")]
    hyp = _make_hyp("temporal", "low", citations)
    response = LLMResponse(status="ok", hypotheses=[hyp])

    result = apply_rules(response, lines)
    final_hyp = result.hypotheses[0]  # type: ignore

    assert final_hyp.evidence_type == "temporal"
    assert final_hyp.confidence == "low"


def test_direct_confidence_unchanged() -> None:
    """Direct evidence keeps the LLM's exact confidence level."""
    lines = [_make_line(1, "ERROR", "Container OOMKilled")]
    citations = [Citation(line_id=1, snippet="OOMKilled")]
    
    # Test all three levels
    for level in ["high", "medium", "low"]:
        hyp = _make_hyp("direct", level, citations)
        response = LLMResponse(status="ok", hypotheses=[hyp])

        result = apply_rules(response, lines)
        final_hyp = result.hypotheses[0]  # type: ignore

        assert final_hyp.evidence_type == "direct"
        assert final_hyp.confidence == level


def test_unknowns_time_window() -> None:
    """Short time window on service logs should generate an unknown."""
    now = datetime.now()
    # 2 minutes apart
    lines = [
        _make_line(1, "INFO", "start").model_copy(update={"timestamp": now, "source_format": "plaintext"}),
        _make_line(2, "ERROR", "fail").model_copy(update={"timestamp": now + timedelta(minutes=2), "source_format": "plaintext"}),
    ]
    response = LLMResponse(status="ok", hypotheses=[])
    
    result = apply_rules(response, lines)
    assert any("logs cover only 2 minutes" in u for u in result.unknowns)


def test_unknowns_external_dep() -> None:
    """External dependency keywords should generate unknowns."""
    now = datetime.now()
    lines = [
        _make_line(1, "INFO", "start").model_copy(update={"timestamp": now, "source_format": "plaintext"}),
        _make_line(2, "ERROR", "connection refused to db").model_copy(update={"timestamp": now + timedelta(minutes=10), "source_format": "plaintext"}),
    ]
    citations = [Citation(line_id=2, snippet="connection refused")]
    hyp = _make_hyp("temporal", "medium", citations)
    hyp.cause = "Database connection timeout"
    response = LLMResponse(status="ok", hypotheses=[hyp])
    
    result = apply_rules(response, lines)
    assert any("external dependency" in u for u in result.unknowns)
    assert any("only one service" in u for u in result.unknowns)


def test_time_window_skips_ci() -> None:
    """The short time window rule should not fire on CI logs (e.g. pytest)."""
    now = datetime.now()
    lines = [
        _make_line(1, "INFO", "start").model_copy(update={"timestamp": now, "source_format": "pytest"}),
        _make_line(2, "ERROR", "fail").model_copy(update={"timestamp": now + timedelta(minutes=2), "source_format": "pytest"}),
    ]
    response = LLMResponse(status="ok", hypotheses=[])
    
    result = apply_rules(response, lines)
    # Should be empty because the time window rule is skipped for CI
    assert not any("logs cover only" in u for u in result.unknowns)

def test_generic_advice_flagged() -> None:
    """Generic check_next patterns are detected and suppressed (LT-34)."""
    # These should be flagged as generic
    generic = [
        "Check the network",
        "Restart the service",
        "Investigate further",
        "Monitor the system",
        "Review the logs",
        "Contact support",
        "Try restarting",
        "Look into the error",
        "Check logs",
        "Verify the network",
    ]
    for advice in generic:
        assert is_generic_advice(advice), f"Should be generic: {advice!r}"

    # These should NOT be flagged — they're specific
    specific = [
        "Check /var/log/mysql/error.log for replication lag after the failover at 10:03",
        "Run `kubectl describe pod auth-svc-7f8b` to see why the liveness probe failed",
        "Compare heap dumps before and after the 10:12 GC pause",
        "Verify the intermediate CA cert (CN=DigiCert SHA2) is in the trust chain",
        None,
        "",
    ]
    for advice in specific:
        assert not is_generic_advice(advice), f"Should NOT be generic: {advice!r}"


def test_generic_advice_suppressed_in_apply_rules() -> None:
    """apply_rules sets check_next to None when the LLM gives generic advice."""
    lines = [_make_line(1, "ERROR", "Something broke")]
    citations = [Citation(line_id=1, snippet="broke")]
    hyp = _make_hyp("temporal", "medium", citations)
    response = LLMResponse(
        status="ok",
        hypotheses=[hyp],
        check_next="Restart the service",
    )

    result = apply_rules(response, lines)
    assert result.check_next is None


def test_specific_advice_kept_in_apply_rules() -> None:
    """apply_rules keeps specific check_next advice from the LLM."""
    lines = [_make_line(1, "ERROR", "Something broke")]
    citations = [Citation(line_id=1, snippet="broke")]
    hyp = _make_hyp("temporal", "medium", citations)
    response = LLMResponse(
        status="ok",
        hypotheses=[hyp],
        check_next="Run `kubectl logs auth-svc` to check for TLS handshake errors",
    )

    result = apply_rules(response, lines)
    assert result.check_next == "Run `kubectl logs auth-svc` to check for TLS handshake errors"