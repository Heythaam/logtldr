"""Pydantic models for logtldr's structured data pipeline.

Field names and types follow ARCHITECTURE.md §AD-2. These models are the
wire format between the ingest → render pipeline stages; contracts are
immutable after creation.
"""

from datetime import datetime
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class LogLine(BaseModel):
    """A single parsed log line.

    Example:
        >>> ll = LogLine(
        ...     line_id=1,
        ...     original_text="2024-01-01 INFO ok",
        ...     redacted_text="2024-01-01 INFO ok",
        ...     level="INFO",
        ...     message="ok",
        ...     source_format="plaintext",
        ... )
        >>> ll.level
        'INFO'
    """

    line_id: int = Field(..., description="Original line number before redaction")
    original_text: str = Field(..., description="Verbatim line, for user display only")
    redacted_text: str = Field(
        ..., description="What the LLM sees; secrets replaced with placeholders"
    )
    timestamp: Optional[datetime] = None
    level: str = Field(..., description="One of DEBUG / INFO / WARN / ERROR / FATAL")
    message: str = Field(
        ..., description="Parsed message field; the string drain3 receives"
    )
    source_format: str = Field(
        ..., description="Input format hint: plaintext, json, pytest, docker, npm, ..."
    )


class Template(BaseModel):
    """A Drain3 log template.

    Example:
        >>> t = Template(
        ...     template_id="t1", pattern="<*>", count=5,
        ...     first_occurrence_line_id=1, last_occurrence_line_id=10,
        ... )
        >>> t.count
        5
    """

    template_id: str = Field(..., description="Stable template ID assigned by drain3")
    pattern: str = Field(
        ..., description="Template pattern with wildcards (*, <*>, etc.)"
    )
    count: int = Field(..., description="Frequency in the log stream")
    first_occurrence_line_id: int
    last_occurrence_line_id: int


class Anomaly(BaseModel):
    """A detected anomaly in the log stream.

    Example:
        >>> a = Anomaly(
        ...     type="new_template", line_ids=[1, 2],
        ...     description="previously unseen pattern",
        ... )
        >>> a.type
        'new_template'
        >>> a.template_ids is None
        True
    """

    type: str = Field(
        ...,
        description=(
            '"new_template" | "frequency_spike" | "first_error" '
            '| "grouped_stack_trace"'
        ),
    )
    timestamp: Optional[datetime] = None
    line_ids: List[int] = Field(..., description="Lines involved in this anomaly")
    template_ids: Optional[List[str]] = Field(
        None, description="Template IDs, if the anomaly is template-based"
    )
    description: str = Field(..., description="Human-readable summary")


class CompressedView(BaseModel):
    """What the LLM receives. Deterministically sampled and tokenized.

    Example:
        >>> cv = CompressedView(
        ...     templates=[], anomalies=[], timeline_candidates=[],
        ...     sample_lines=[], statistics={}, metadata={},
        ...     estimated_tokens=0,
        ... )
        >>> cv.estimated_tokens
        0
    """

    templates: List[Dict] = Field(
        ..., description="[{template_id, pattern, count}, ...]"
    )
    anomalies: List[Anomaly]
    timeline_candidates: List[Dict] = Field(
        ..., description="[{timestamp, anomaly_type, description}, ...]"
    )
    sample_lines: List[Dict] = Field(
        ...,
        description=(
            "[{line_id, redacted_text, timestamp, level}, ...] "
            "deterministically selected"
        ),
    )
    truncation_note: Optional[str] = Field(
        None, description='e.g. "500 lines sampled from 100k"'
    )
    statistics: Dict = Field(
        ...,
        description="total_lines, parseable_lines, error_count, warning_count, ...",
    )
    metadata: Dict = Field(
        ..., description="time_window_start, time_window_end, log_format, ..."
    )
    estimated_tokens: int = Field(..., description="Actual token count (not estimate)")


class Citation(BaseModel):
    """A reference to a specific log line used as evidence.

    Example:
        >>> Citation(line_id=3, snippet="disk full").line_id
        3
    """

    line_id: int
    snippet: str = Field(
        ...,
        description="Short verbatim excerpt from the redacted line, as cited by the LLM",
    )


class Hypothesis(BaseModel):
    """A candidate root-cause hypothesis produced by the LLM.

    Example:
        >>> h = Hypothesis(
        ...     cause="disk full", evidence_type="direct",
        ...     confidence="high", citations=[],
        ... )
        >>> h.evidence_type
        'direct'
    """

    cause: str
    evidence_type: str = Field(
        ..., description='"direct" (code-enforced) or "temporal"'
    )
    confidence: str = Field(
        ...,
        description='"high" | "medium" | "low"; temporal hypotheses capped by code',
    )
    citations: List[Citation]


class LLMResponse(BaseModel):
    """Raw output from the LLM (before validation and rules).

    Example:
        >>> r = LLMResponse(status="ok")
        >>> r.hypotheses is None
        True
    """

    status: str = Field(
        ..., description='"ok" | "nothing_unusual" | "not_enough_signal"'
    )
    verdict: Optional[str] = None
    hypotheses: Optional[List[Hypothesis]] = Field(
        None, description="LLM proposals; may not all survive validation"
    )
    timeline: Optional[List[Dict]] = Field(
        None, description="[{time, event, citations}, ...]"
    )
    check_next: Optional[str] = None


class FinalSummary(BaseModel):
    """Final output: after citation validation and rules engine.

    Example:
        >>> FinalSummary(status="ok").unknowns
        []
    """

    status: str = Field(
        ..., description='"ok" | "nothing_unusual" | "not_enough_signal"'
    )
    verdict: Optional[str] = None
    hypotheses: Optional[List[Hypothesis]] = Field(
        None, description="Top shown in terminal; all in --json"
    )
    timeline: Optional[List[Dict]] = Field(
        None, description="[{time, event, citations}, ...]"
    )
    unknowns: List[str] = Field(
        default_factory=list, description="Rule-based unknowns"
    )
    check_next: Optional[str] = None