"""Secret redaction (AD-6)."""

from logtldr.models import LogLine


# TODO(LT-REDACT): Replace with actual regex/placeholder logic from AD-6.
def redact(lines: list[LogLine]) -> list[LogLine]:
    """Replace secrets with placeholders. Stub currently does nothing."""
    for line in lines:
        line.redacted_text = line.original_text
    return lines