"""Rules engine (AD-5)."""

import re
from datetime import timedelta
from importlib.resources import files

import yaml

from logtldr.models import FinalSummary, Hypothesis, LLMResponse, LogLine

# Load the direct-cause catalog once at module level
try:
    _yaml_path = files("logtldr").joinpath("direct_causes.yaml")
    _catalog: list[dict] = yaml.safe_load(_yaml_path.read_text(encoding="utf-8"))
except Exception:
    _catalog = []

# CI formats don't have a meaningful time window
_CI_FORMATS = ("pytest", "npm", "json")
# Keywords indicating an external dependency issue
_EXTERNAL_KEYWORDS = ("timeout", "connection refused", "unreachable", "external", "upstream", "dependency")


# Deploy/startup marker patterns (LT-33)
_DEPLOY_MARKERS = [
    re.compile(r"starting\s+v?(\d+\.\d+[\.\d]*)", re.I),
    re.compile(r"deployed\s+v?(\d+\.\d+[\.\d]*)", re.I),
    re.compile(r"version\s+v?(\d+\.\d+[\.\d]*)\s+(started|deployed|rolling)", re.I),
    re.compile(r"release\s+v?(\d+\.\d+[\.\d]*)", re.I),
    re.compile(r"config\s+reloaded", re.I),
    re.compile(r"initialized\s+successfully", re.I),
    re.compile(r"server\s+started", re.I),
    re.compile(r"application\s+started", re.I),
    re.compile(r"rolling\s+update.*v?(\d+\.\d+[\.\d]*)", re.I),
]

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
            
        if line.level not in ("WARN", "ERROR", "FATAL"):
            continue
            
        if _matches_catalog(line.message) or _matches_catalog(citation.snippet):
            return True
            
    return False


def cap_confidence(evidence_type: str, model_confidence: str) -> str:
    """Cap confidence based on evidence type (LT-26)."""
    if evidence_type == "temporal":
        if model_confidence == "high":
            return "medium"
        return model_confidence
    return model_confidence


def _generate_unknowns(lines: list[LogLine], hypotheses: list[Hypothesis]) -> list[str]:
    """Deterministically generate unknowns based on log characteristics (LT-28)."""
    unknowns: list[str] = []
    if not lines:
        return unknowns

    # 1. Short time window rule (< 5 min, service logs only)
    first_ts = lines[0].timestamp
    last_ts = lines[-1].timestamp
    
    if first_ts and last_ts and lines[0].source_format not in _CI_FORMATS:
        window = last_ts - first_ts
        if window < timedelta(minutes=5):
            mins = int(window.total_seconds() / 60)
            unknowns.append(f"logs cover only {mins} minutes; cause may be outside this window")

    # 2. Logs end abruptly
    # If the last line is an ERROR/FATAL but doesn't look like a graceful shutdown
    last_line = lines[-1]
    if last_line.level in ("ERROR", "FATAL"):
        graceful = ("shutdown", "exit", "terminated", "goodbye")
        if not any(kw in last_line.message.lower() for kw in graceful):
            unknowns.append("logs end abruptly (possible truncation)")

    # 3 & 4. Evidence points to an external dependency
    if hypotheses:
        top_hyp = hypotheses[0]
        # Check if cause or citations mention external dep keywords
        hyp_text = top_hyp.cause.lower()
        cit_text = " ".join([c.snippet.lower() for c in top_hyp.citations])
        combined_text = hyp_text + " " + cit_text
        
        points_to_external = any(kw in combined_text for kw in _EXTERNAL_KEYWORDS)
        
        if points_to_external:
            unknowns.append("errors point to an external dependency; check its logs separately")
            
            # If we can't see evidence of the dependency in our logs (single service)
            # A simple heuristic: if the log format is plaintext and we have no IPs of other services.
            # We'll just assume single-service for this MVP.
            unknowns.append("only one service's logs present; evidence points to a dependency—check its logs separately")

    return unknowns


def apply_rules(response: LLMResponse, lines: list[LogLine]) -> FinalSummary:
    """Apply rules to downgrade evidence, cap confidence, and generate unknowns."""
    hypotheses = response.hypotheses or []
    
    for hyp in hypotheses:
        if hyp.evidence_type == "direct":
            if not _is_direct_evidence(hyp, lines):
                hyp.evidence_type = "temporal"
                
        hyp.confidence = cap_confidence(hyp.evidence_type, hyp.confidence)

    unknowns = _generate_unknowns(lines, hypotheses)

    # Deploy/startup marker detection (LT-33)
    deploy_marker = detect_deploy_marker(lines)
    if deploy_marker:
        timing_note = _deploy_timing_note(deploy_marker, lines)
        if timing_note:
            unknowns.insert(0, timing_note)

    return FinalSummary(
        status=response.status,
        verdict=response.verdict,
        hypotheses=hypotheses,
        timeline=response.timeline,
        unknowns=unknowns,
        check_next=response.check_next,
    )

def detect_deploy_marker(lines: list[LogLine]) -> dict | None:
    """Find the latest deploy/startup marker and return its metadata (LT-33)."""
    for line in lines:
        text = line.message or line.original_text
        for pat in _DEPLOY_MARKERS:
            m = pat.search(text)
            if m:
                version = m.group(1) if m.lastindex else None
                label = f"v{version}" if version else "deploy/restart"
                return {
                    "line_id": line.line_id,
                    "label": label,
                    "timestamp": line.timestamp,
                }
    return None


def _deploy_timing_note(marker: dict, lines: list[LogLine]) -> str | None:
    """Generate a timing note like 'Errors started 40s after v2.3.1 started.' (LT-33)."""
    marker_ts = marker.get("timestamp")
    if not marker_ts:
        return None

    # Find first ERROR/FATAL after the marker
    for line in lines:
        if line.line_id <= marker["line_id"]:
            continue
        if line.level in ("ERROR", "FATAL") and line.timestamp:
            delta = line.timestamp - marker_ts
            secs = int(delta.total_seconds())
            if secs < 0:
                continue
            if secs < 60:
                time_str = f"{secs}s"
            elif secs < 3600:
                time_str = f"{secs // 60}m{secs % 60}s"
            else:
                time_str = f"{secs // 3600}h{(secs % 3600) // 60}m"
            return f"Errors started {time_str} after {marker['label']} started."
    return None