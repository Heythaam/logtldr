"""Citation validation (AD-1)."""

from logtldr.models import LLMResponse, LogLine


def validate_citations(response: LLMResponse, lines: list[LogLine]) -> LLMResponse:
    """Verify citations against the redacted logs.

    - Checks that line_id exists and the snippet matches exactly in redacted_text.
    - Marks `citation_valid` on each Citation.
    - Drops any Hypothesis that has no valid citations.
    - If no hypotheses remain, changes status to 'not_enough_signal'.
    """
    if not response.hypotheses:
        # If the LLM returned "ok" but no hypotheses, that's a weak answer.
        if response.status == "ok":
            response.status = "not_enough_signal"
        return response

    # Map line_id to the LogLine object for O(1) lookup
    line_dict = {line.line_id: line for line in lines}
    valid_hypotheses = []

    for hyp in response.hypotheses:
        has_valid_citation = False
        
        for citation in hyp.citations:
            line = line_dict.get(citation.line_id)
            if line and citation.snippet in line.redacted_text:
                citation.citation_valid = True
                has_valid_citation = True
            else:
                citation.citation_valid = False

        # Keep the hypothesis only if it has at least one valid citation
        if has_valid_citation:
            valid_hypotheses.append(hyp)

    # If the top hypothesis was dropped and none remain, we refuse to guess
    if not valid_hypotheses:
        response.status = "not_enough_signal"

    # Assign the surviving hypotheses back to the response
    response.hypotheses = valid_hypotheses if valid_hypotheses else None
    
    return response