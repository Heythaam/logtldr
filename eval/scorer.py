"""Eval scorer (LT-21).

Scores a FinalSummary against a case's expected outcome.

Case kinds, derived from the expected status:
- "diagnosis" (expected "ok"): correct = status "ok" AND every cause keyword
  appears in the top hypothesis AND every citation on it is valid.
- "healthy" (expected "nothing_unusual"): correct = status "nothing_unusual".
  A summary with status "ok" is a FALSE POSITIVE (a cause invented for a
  healthy log).
- "unknown" (expected "not_enough_signal"): scored separately from accuracy.
  Correct = status "not_enough_signal"; status "ok" counts as fabricated.

Citations are re-checked against the redacted lines with the same rule as
logtldr.citation_validator (line exists, snippet is a substring of
redacted_text) instead of trusting the pipeline's own citation_valid flag:
the eval must not take the system's word for the thing it is grading.
"""

from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from logtldr.models import Citation, FinalSummary, Hypothesis, LogLine

CaseKind = Literal["diagnosis", "healthy", "unknown"]

_KIND_BY_STATUS: dict[str, CaseKind] = {
    "ok": "diagnosis",
    "nothing_unusual": "healthy",
    "not_enough_signal": "unknown",
}


class ExpectedOutcome(BaseModel):
    """Ground truth for one eval case."""

    case_id: str
    expected_status: Literal["ok", "nothing_unusual", "not_enough_signal"]
    cause_keywords: list[str] = Field(
        default_factory=list,
        description="All must appear (case-insensitive) in the top hypothesis cause",
    )

    @property
    def kind(self) -> CaseKind:
        return _KIND_BY_STATUS[self.expected_status]


@dataclass(frozen=True)
class CaseScore:
    """Result of scoring one case."""

    case_id: str
    kind: CaseKind
    actual_status: str
    correct: bool
    cause_match: bool = False
    citations_valid: bool = False
    false_positive: bool = False
    fabricated: bool = False
    missing_keywords: tuple[str, ...] = ()
    reason: str = ""


@dataclass
class ScoreReport:
    """Aggregate over a run. Unknowns never affect accuracy."""

    cases: list[CaseScore] = field(default_factory=list)

    def _of(self, kind: CaseKind) -> list[CaseScore]:
        return [c for c in self.cases if c.kind == kind]

    @property
    def diagnosis_total(self) -> int:
        return len(self._of("diagnosis"))

    @property
    def diagnosis_correct(self) -> int:
        return sum(c.correct for c in self._of("diagnosis"))

    @property
    def healthy_total(self) -> int:
        return len(self._of("healthy"))

    @property
    def healthy_correct(self) -> int:
        return sum(c.correct for c in self._of("healthy"))

    @property
    def false_positives(self) -> int:
        return sum(c.false_positive for c in self._of("healthy"))

    @property
    def unknown_total(self) -> int:
        return len(self._of("unknown"))

    @property
    def unknown_correct(self) -> int:
        return sum(c.correct for c in self._of("unknown"))

    @property
    def fabricated(self) -> int:
        return sum(c.fabricated for c in self._of("unknown"))

    @property
    def accuracy(self) -> float:
        """Correct / scored, over diagnosis + healthy cases (unknowns excluded)."""
        scored = self.diagnosis_total + self.healthy_total
        if scored == 0:
            return 0.0
        return (self.diagnosis_correct + self.healthy_correct) / scored

    def to_dict(self) -> dict[str, object]:
        return {
            "accuracy": round(self.accuracy, 4),
            "diagnosis": {"correct": self.diagnosis_correct, "total": self.diagnosis_total},
            "healthy": {
                "correct": self.healthy_correct,
                "total": self.healthy_total,
                "false_positives": self.false_positives,
            },
            "unknown": {
                "correct": self.unknown_correct,
                "total": self.unknown_total,
                "fabricated": self.fabricated,
            },
            "cases": [c.__dict__ | {"missing_keywords": list(c.missing_keywords)}
                      for c in self.cases],
        }


def _citation_ok(citation: Citation, line_dict: dict[int, LogLine]) -> bool:
    line = line_dict.get(citation.line_id)
    return line is not None and citation.snippet in line.redacted_text


def _citations_valid(hypothesis: Hypothesis, lines: list[LogLine]) -> bool:
    """True iff the hypothesis has at least one citation and all are valid."""
    if not hypothesis.citations:
        return False
    line_dict = {line.line_id: line for line in lines}
    return all(_citation_ok(c, line_dict) for c in hypothesis.citations)


def _missing_keywords(cause: str, keywords: list[str]) -> tuple[str, ...]:
    text = cause.casefold()
    return tuple(k for k in keywords if k.casefold() not in text)


def score_case(
    expected: ExpectedOutcome, summary: FinalSummary, lines: list[LogLine]
) -> CaseScore:
    """Score one FinalSummary against its expected outcome."""
    kind = expected.kind
    status = summary.status

    if kind == "healthy":
        fp = status == "ok"
        return CaseScore(
            case_id=expected.case_id, kind=kind, actual_status=status,
            correct=status == "nothing_unusual", false_positive=fp,
            reason="false positive: cause reported for healthy log" if fp else "",
        )

    if kind == "unknown":
        fabricated = status == "ok"
        return CaseScore(
            case_id=expected.case_id, kind=kind, actual_status=status,
            correct=status == "not_enough_signal", fabricated=fabricated,
            reason="cause reported where none is determinable" if fabricated else "",
        )

    # diagnosis
    if status != "ok" or not summary.hypotheses:
        return CaseScore(
            case_id=expected.case_id, kind=kind, actual_status=status, correct=False,
            missing_keywords=tuple(expected.cause_keywords),
            reason=f"expected a diagnosis, got status={status!r}",
        )

    top = summary.hypotheses[0]
    missing = _missing_keywords(top.cause, expected.cause_keywords)
    cause_match = not missing
    citations_valid = _citations_valid(top, lines)

    reasons = []
    if not cause_match:
        reasons.append(f"missing keywords: {', '.join(missing)}")
    if not citations_valid:
        reasons.append("top hypothesis has missing or invalid citations")

    return CaseScore(
        case_id=expected.case_id, kind=kind, actual_status=status,
        correct=cause_match and citations_valid,
        cause_match=cause_match, citations_valid=citations_valid,
        missing_keywords=missing, reason="; ".join(reasons),
    )


def score_run(
    results: list[tuple[ExpectedOutcome, FinalSummary, list[LogLine]]],
) -> ScoreReport:
    """Score a whole eval run."""
    return ScoreReport(cases=[score_case(e, s, lines) for e, s, lines in results])