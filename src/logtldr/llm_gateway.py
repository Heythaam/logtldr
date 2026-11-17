"""LiteLLM integration (AD-4)."""

from logtldr.models import Anomaly, CompressedView, LLMResponse, LogLine, Template


# TODO(LT-LLM): Replace with actual LiteLLM calls, JSON parsing, and localhost check.
def build_compressed_view(
    lines: list[LogLine], templates: list[Template], anomalies: list[Anomaly]
) -> CompressedView:
    """Helper to build the view. Stub creates a minimal object."""
    return CompressedView(
        templates=[{"template_id": t.template_id, "pattern": t.pattern, "count": t.count} for t in templates],
        anomalies=anomalies,
        timeline_candidates=[],
        sample_lines=[{"line_id": line.line_id, "redacted_text": line.redacted_text} for line in lines[:5]],
        statistics={"total_lines": len(lines)},
        metadata={"log_format": "unknown"},
        estimated_tokens=len(lines) * 10,
    )

# TODO(LT-LLM): Replace with actual LiteLLM calls.
def summarize(view: CompressedView) -> LLMResponse:
    """Call the LLM. Stub returns a fixed response."""
    return LLMResponse(
        status="ok",
        verdict="Stub LLM response: pipeline is wired!"
    )