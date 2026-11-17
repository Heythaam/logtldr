"""Citation validation (AD-1)."""

from logtldr.models import LLMResponse, LogLine


# TODO(LT-CITATION): Replace with exact snippet matching against redacted_text.
def validate_citations(response: LLMResponse, lines: list[LogLine]) -> LLMResponse:
    """Validate citations. Stub just passes the response through."""
    return response