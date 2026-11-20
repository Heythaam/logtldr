"""Output formatting (AD-10)."""

from logtldr.models import FinalSummary


# TODO(LT-RENDER): Replace with TerminalRenderer and JSONRenderer classes from AD-10.
def render_terminal(summary: FinalSummary) -> str:
    return summary.verdict or "No verdict."

def render_json(summary: FinalSummary) -> str:
    return summary.model_dump_json()