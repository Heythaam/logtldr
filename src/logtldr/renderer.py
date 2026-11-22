"""Output formatting (AD-10)."""

from logtldr.models import FinalSummary


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


def render_terminal(summary: FinalSummary) -> str:
    """Render the final summary for the terminal."""
    return format_verdict(summary)


def render_json(summary: FinalSummary) -> str:
    """Render the final summary as a JSON string."""
    return summary.model_dump_json()