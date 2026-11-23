"""Output formatting (AD-10)."""

import shutil
import textwrap
from typing import List

from logtldr.models import FinalSummary

MAX_LINES = 30

# ANSI color codes
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
RESET = "\033[0m"


def _get_width() -> int:
    """Get terminal width, default 80."""
    try:
        return shutil.get_terminal_size().columns
    except Exception:
        return 80


def _wrap(text: str, width: int, indent: str = "") -> str:
    """Wrap text to terminal width with optional indent for continuation lines."""
    return textwrap.fill(
        text,
        width=width,
        initial_indent="",
        subsequent_indent=indent,
    )


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


def _status_color(status: str) -> str:
    """Return ANSI color for a status."""
    if status == "ok":
        return RED  # there IS a problem
    elif status == "nothing_unusual":
        return GREEN
    elif status == "not_enough_signal":
        return YELLOW
    return ""


def render_terminal(summary: FinalSummary) -> str:
    """Render the final summary for the terminal (LT-31).

    Layout (fits ~30 lines, adapts to terminal width):
        ┌─ logtldr ─────────────────────────┐
        │ Status: ok                         │
        │                                    │
        │ Verdict:                           │
        │   Evidence points to ...           │
        │                                    │
        │ Citations:                         │
        │   [line 16] "ERROR: value too..."  │
        │                                    │
        │ Check next:                        │
        │   Check the PostgreSQL database... │
        └────────────────────────────────────┘

    All text uses redacted content only — no secrets in output.
    """
    width = _get_width()
    inner = width - 4  # padding for "│ " and " │"
    lines: List[str] = []

    # Header
    header_text = " logtldr "
    dash_count = width - 2 - len(header_text)
    left_dashes = 2
    right_dashes = dash_count - left_dashes
    lines.append(f"{DIM}{'─' * left_dashes}{RESET}{BOLD}{header_text}{RESET}{DIM}{'─' * right_dashes}{RESET}")

    # Status
    sc = _status_color(summary.status)
    lines.append(f"  {BOLD}Status:{RESET} {sc}{summary.status}{RESET}")
    lines.append("")

    # Verdict
    verdict = format_verdict(summary)
    lines.append(f"  {BOLD}Verdict:{RESET}")
    wrapped = _wrap(verdict, width=inner, indent="    ")
    lines.append(f"    {wrapped.strip()}")

    # Citations from top hypothesis
    if summary.status == "ok" and summary.hypotheses:
        top_hyp = summary.hypotheses[0]
        if top_hyp.citations:
            lines.append("")
            lines.append(f"  {BOLD}Evidence:{RESET}")
            for citation in top_hyp.citations[:5]:  # max 5 citations
                snippet = citation.snippet
                if len(snippet) > inner - 15:
                    snippet = snippet[:inner - 18] + "..."
                lines.append(
                    f"    {DIM}line {citation.line_id}:{RESET} {CYAN}\"{snippet}\"{RESET}"
                )

        # Secondary hypotheses (if any)
        if len(summary.hypotheses) > 1:
            lines.append("")
            lines.append(f"  {BOLD}Also:{RESET}")
            for hyp in summary.hypotheses[1:3]:  # max 2 more
                cause_short = hyp.cause.strip().rstrip(".")
                if len(cause_short) > inner - 6:
                    cause_short = cause_short[:inner - 9] + "..."
                lines.append(f"    {DIM}•{RESET} {cause_short} {DIM}({hyp.confidence}){RESET}")

    # Timeline
    tl = format_timeline(summary)
    if tl:
        lines.append("")
        lines.append(f"  {BOLD}Timeline:{RESET}")
        wrapped_tl = _wrap(tl, width=inner, indent="    ")
        lines.append(f"    {wrapped_tl.strip()}")

    # Unknowns
    if summary.unknowns:
        lines.append("")
        lines.append(f"  {BOLD}Unknowns:{RESET}")
        for unknown in summary.unknowns:
            wrapped_u = _wrap(unknown, width=inner - 4, indent="      ")
            lines.append(f"    {YELLOW}•{RESET} {wrapped_u.strip()}")

    # Check next
    if summary.check_next:
        lines.append("")
        lines.append(f"  {BOLD}Check next:{RESET}")
        wrapped_cn = _wrap(summary.check_next, width=inner, indent="    ")
        lines.append(f"    {wrapped_cn.strip()}")

    # Footer
    lines.append("")
    lines.append(f"{DIM}{'─' * width}{RESET}")

    return "\n".join(lines)


def render_json(summary: FinalSummary) -> str:
    """Render JSON output, colorized when printing to terminal (LT-32)."""
    import json
    import sys

    output: dict = {
        "status": summary.status,
        "verdict": format_verdict(summary),
    }

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

    if summary.timeline:
        output["timeline"] = _sort_timeline_events(summary.timeline)
    else:
        output["timeline"] = []

    output["unknowns"] = summary.unknowns or []
    output["check_next"] = summary.check_next

    raw = json.dumps(output, indent=2)

    # Only colorize when outputting to a real terminal, not a pipe
    if not sys.stdout.isatty():
        return raw

    import re

    # Keys: bold cyan
    raw = re.sub(r'"([^"]+)"(?=\s*:)', f'{BOLD}{CYAN}"\\1"{RESET}', raw)
    # String values: green
    raw = re.sub(r':\s*"([^"]*)"', lambda m: f': {GREEN}"{m.group(1)}"{RESET}', raw)
    # Numbers: yellow
    raw = re.sub(r':\s*(\d+)', lambda m: f': {YELLOW}{m.group(1)}{RESET}', raw)
    # Booleans/null: dim
    raw = re.sub(r':\s*(true|false|null)', lambda m: f': {DIM}{m.group(1)}{RESET}', raw)
    # Brackets: dim
    raw = raw.replace('[', f'{DIM}[{RESET}').replace(']', f'{DIM}]{RESET}')
    raw = raw.replace('{', f'{DIM}{{{RESET}').replace('}', f'{DIM}}}{RESET}')

    return raw