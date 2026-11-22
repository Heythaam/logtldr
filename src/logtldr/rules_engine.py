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
    """Check if the hypothesis is backed by a valid direct-cause citation.
    
    A hypothesis qualifies as "direct" only if at least one of its cited
    lines is WARN/ERROR/FATAL AND contains a keyword from the catalog.
    """
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


def apply_rules(response: LLMResponse, lines: list[LogLine]) -> FinalSummary:
    """Apply rules to downgrade evidence and cap confidence.
    
    - Code accepts "direct" only if verified against the catalog.
    - Unverified "direct" claims become "temporal".
    - All "temporal" claims are capped at "medium" confidence.
    """
    hypotheses = response.hypotheses or []
    
    for hyp in hypotheses:
        if hyp.evidence_type == "direct":
            if not _is_direct_evidence(hyp, lines):
                # The LLM lied or was mistaken; downgrade it
                hyp.evidence_type = "temporal"
                
        if hyp.evidence_type == "temporal":
            # Temporal evidence can never be "high" confidence
            if hyp.confidence == "high":
                hyp.confidence = "medium"

    return FinalSummary(
        status=response.status,
        verdict=response.verdict,
        hypotheses=hypotheses,
        timeline=response.timeline,
        unknowns=[],
        check_next=response.check_next,
    )