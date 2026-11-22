"""Rules engine (AD-5)."""

from importlib.resources import files

import yaml

from logtldr.models import FinalSummary, Hypothesis, LLMResponse, LogLine

# Load the direct-cause catalog once at module level
try:
    _yaml_path = files("logtldr").joinpath("direct_causes.yaml")
    _catalog: list[dict] = yaml.safe_load(_yaml_path.read_text(encoding="utf-8"))
except Exception:
    _catalog = []


def _matches_catalog(text: str) -> bool:
    """Check if the text contains any keyword from the catalog."""
    text_lower = text.lower()
    for entry in _catalog:
        for kw in entry.get("keywords", []):
            if kw.lower() in text_lower:
                return True
    return False


def _is_direct_evidence(hypothesis: Hypothesis, lines: list[LogLine]) -> bool:
    """Check if the hypothesis is backed by a valid direct-cause citation."""
    if not hypothesis.citations:
        return False

    line_dict = {line.line_id: line for line in lines}
    
    for citation in hypothesis.citations:
        line = line_dict.get(citation.line_id)
        if not line:
            continue
            
        # 1. Check if the line level is severe enough
        if line.level not in ("WARN", "ERROR", "FATAL"):
            continue
            
        # 2. Check if the message or snippet matches the catalog
        if _matches_catalog(line.message) or _matches_catalog(citation.snippet):
            return True
            
    return False


def cap_confidence(evidence_type: str, model_confidence: str) -> str:
    """Cap confidence based on evidence type (LT-26).

    Code can only downgrade, never upgrade.
    Temporal evidence is capped at 'medium'.
    Direct evidence keeps the LLM's level.
    """
    if evidence_type == "temporal":
        if model_confidence == "high":
            return "medium"
        return model_confidence
    return model_confidence


def apply_rules(response: LLMResponse, lines: list[LogLine]) -> FinalSummary:
    """Apply rules to downgrade evidence and cap confidence."""
    hypotheses = response.hypotheses or []
    
    for hyp in hypotheses:
        if hyp.evidence_type == "direct":
            if not _is_direct_evidence(hyp, lines):
                # The LLM lied or was mistaken; downgrade it
                hyp.evidence_type = "temporal"
                
        # Enforce confidence cap based on (possibly updated) evidence type
        hyp.confidence = cap_confidence(hyp.evidence_type, hyp.confidence)

    return FinalSummary(
        status=response.status,
        verdict=response.verdict,
        hypotheses=hypotheses,
        timeline=response.timeline,
        unknowns=[],
        check_next=response.check_next,
    )