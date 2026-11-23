"""Output formatting (AD-10)."""

from typing import List

from logtldr.models import FinalSummary

MAX_WIDTH = 80
MAX_LINES = 30


def format_verdict(summary: FinalSummary) -> str:
    """Format the verdict deterministically (LT-29).

    Enforces the format: "Evidence points to <cause>, confidence: <level>".
    """
    if summary.status == "ok" and summary.hypotheses:
        top_hyp = summary.hypotheses[0]

        # Sanitize the LLM's cause to ensure it never says "root cause is"
        # and doesn't have awkward capitalization/punctuation.
        cause = top_hyp.cause.strip().rstrip(".")
        if cause.lower().startswith("the root cause is "):
            cause = cause[18:]  # "The root cause is " = 18 chars
        cause = cause.replace("root cause is", "likely")

        return f"Evidence points to {cause}, confidence: {top_hyp.confidence}"

    elif summary.status == "nothing_unusual":
        return "Nothing unusual detected in the logs."

    elif summary.status == "not_enough_signal":
        return "Not enough signal to determine a cause."

    return summary.verdict or "Analysis complete."


def format_timeline(summary: FinalSummary) -> str:
    """Format a chronological timeline starting at the first anomaly (LT-30).

    Each event is rendered as:
        line <N>: <event>

    Multiple events are joined with " → " to show the causal chain.
    Events are sorted by their first cited line number (chronological order).
    Returns an empty string if there is no timeline.
    """
    if not summary.timeline:
        return ""

    # Sort events by the earliest cited line number (chronological).
    # Each event dict has: {time, event, citations: [{line_id, snippet}]}
    sorted_events = _sort_timeline_events(summary.timeline)

    parts: List[str] = []
    for evt in sorted_events:
        line_id = _first_line_id(evt)
        label = evt.get("event", "unknown event")
        if line_id is not None:
            parts.append(f"line {line_id}: {label}")
        else:
            parts.append(label)

    return " → ".join(parts)


def _first_line_id(event: dict) -> int | None:
    """Return the smallest line_id from an event's citations, or None."""
    citations = event.get("citations", [])
    if not citations:
        return None
    line_ids = [c["line_id"] for c in citations if "line_id" in c]
    return min(line_ids) if line_ids else None


def _sort_timeline_events(timeline: list) -> list:
    """Sort timeline events by their first cited line number."""
    def sort_key(evt: dict) -> int:
        lid = _first_line_id(evt)
        # Events without citations go to the end
        return lid if lid is not None else float("inf")  # type: ignore[return-value]

    return sorted(timeline, key=sort_key)


def _truncate(text: str, width: int = MAX_WIDTH) -> str:
    """Truncate a string to fit within width, adding ellipsis if needed."""
    if len(text) <= width:
        return text
    return text[: width - 3] + "..."


def render_terminal(summary: FinalSummary) -> str:
    """Render the final summary for the terminal (LT-31).

    Layout (fits ~30 lines, 80 chars wide):
        Status: <status>
        Verdict: <formatted verdict>
        Timeline: <causal chain>         (if present)
        Unknowns:                        (if present)
          - <unknown 1>
          - <unknown 2>
        Check next: <suggestion>         (if present)

    All text uses redacted content only — no secrets in output.
    """
    lines: List[str] = []

    # Status
    lines.append(_truncate(f"Status: {summary.status}"))

    # Verdict
    verdict = format_verdict(summary)
    lines.append(_truncate(f"Verdict: {verdict}"))

    # Timeline
    tl = format_timeline(summary)
    if tl:
        lines.append("")
        lines.append(_truncate(f"Timeline: {tl}"))

    # Unknowns
    if summary.unknowns:
        lines.append("")
        lines.append("Unknowns:")
        for unknown in summary.unknowns:
            lines.append(_truncate(f"  - {unknown}"))

    # Check next
    if summary.check_next:
        lines.append("")
        lines.append(_truncate(f"Check next: {summary.check_next}"))

    return "\n".join(lines)


def render_json(summary: FinalSummary) -> str:
    """Render the full JSON output (LT-32).

    Includes: status, verdict (formatted), all hypotheses, timeline
    (sorted chronologically), unknowns, and check_next.
    Output is valid JSON parseable by ``python -m json.tool``.
    """
    import json

    output: dict = {
        "status": summary.status,
        "verdict": format_verdict(summary),
    }

    # All hypotheses (not just top)
    if summary.hypotheses:
        output["hypotheses"] = [
            {
                "cause": h.cause,
                "evidence_type": h.evidence_type,
                "confidence": h.confidence,
                "citations": [
                    {"line_id": c.line_id, "snippet": c.snippet}
                    for c in h.citations
                ],
            }
            for h in summary.hypotheses
        ]
    else:
        output["hypotheses"] = []

    # Timeline (sorted)
    if summary.timeline:
        output["timeline"] = _sort_timeline_events(summary.timeline)
    else:
        output["timeline"] = []

    # Unknowns
    output["unknowns"] = summary.unknowns or []

    # Check next
    output["check_next"] = summary.check_next

    return json.dumps(output, indent=2)