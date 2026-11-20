"""Eval scorer (LT-21).

Scores a FinalSummary against a case's expected outcome.

Case kinds, derived from the expected status:
- "diagnosis" (expected "ok"): correct = status "ok" AND the LLM's cited
  line_ids overlap with the ground-truth evidence_line_ids (≥1 common line)
  AND every citation on the top hypothesis is valid.
  Keywords are checked as a soft signal against the full response text
  (verdict + cause + citation snippets) but do NOT affect correctness.
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
        description="Soft signal: checked against full response text but not required for correct",
    )
    evidence_line_ids: list[int] = Field(
        default_factory=list,
        description="Ground-truth line IDs the LLM should cite (≥1 overlap required)",
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
    evidence_overlap: bool = False
    evidence_matched: tuple[int, ...] = ()
    evidence_expected: tuple[int, ...] = ()
    citations_valid: bool = False
    keywords_matched: bool = False
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
            "cases": [
                c.__dict__
                | {
                    "missing_keywords": list(c.missing_keywords),
                    "evidence_matched": list(c.evidence_matched),
                    "evidence_expected": list(c.evidence_expected),
                }
                for c in self.cases
            ],
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


def _cited_line_ids(summary: FinalSummary) -> set[int]:
    """Collect all line_ids cited across all hypotheses."""
    ids: set[int] = set()
    for hyp in summary.hypotheses or []:
        for cit in hyp.citations or []:
            ids.add(cit.line_id)
    return ids


def _evidence_overlap(
    summary: FinalSummary, expected_ids: list[int]
) -> tuple[bool, tuple[int, ...]]:
    """Check if cited lines overlap with expected evidence lines.

    Returns (has_overlap, matched_ids).
    If no evidence_line_ids in ground truth, skip the check (pass).
    """
    if not expected_ids:
        # No evidence lines specified — can't check, treat as pass
        return True, ()
    cited = _cited_line_ids(summary)
    matched = sorted(cited & set(expected_ids))
    return len(matched) >= 1, tuple(matched)


def _full_response_text(summary: FinalSummary) -> str:
    """Build searchable text from the full LLM response for keyword soft-match."""
    parts: list[str] = []
    if summary.verdict:
        parts.append(summary.verdict)
    for hyp in summary.hypotheses or []:
        parts.append(hyp.cause)
        for cit in hyp.citations or []:
            parts.append(cit.snippet)
    return " ".join(parts).casefold()


def _missing_keywords(full_text: str, keywords: list[str]) -> tuple[str, ...]:
    """Check keywords against the full response text (casefold)."""
    return tuple(k for k in keywords if k.casefold() not in full_text)


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

    # --- diagnosis ---
    if status != "ok" or not summary.hypotheses:
        return CaseScore(
            case_id=expected.case_id, kind=kind, actual_status=status, correct=False,
            evidence_expected=tuple(expected.evidence_line_ids),
            reason=f"expected a diagnosis, got status={status!r}",
        )

    top = summary.hypotheses[0]

    # Tier 2: evidence-line overlap (primary correctness signal)
    has_overlap, matched = _evidence_overlap(summary, expected.evidence_line_ids)

    # Citations validity (re-checked independently)
    citations_valid = _citations_valid(top, lines)

    # Tier 3: keywords as soft signal (not required for correct)
    full_text = _full_response_text(summary)
    missing = _missing_keywords(full_text, expected.cause_keywords)
    keywords_matched = not missing

    # Correct = status ok + evidence overlap + citations valid
    correct = has_overlap and citations_valid

    reasons = []
    if not has_overlap:
        cited = sorted(_cited_line_ids(summary))
        reasons.append(
            f"no evidence overlap: cited {cited}, expected {expected.evidence_line_ids}"
        )
    if not citations_valid:
        reasons.append("top hypothesis has missing or invalid citations")
    if not keywords_matched:
        reasons.append(f"soft: missing keywords: {', '.join(missing)}")

    return CaseScore(
        case_id=expected.case_id, kind=kind, actual_status=status,
        correct=correct,
        evidence_overlap=has_overlap,
        evidence_matched=matched,
        evidence_expected=tuple(expected.evidence_line_ids),
        citations_valid=citations_valid,
        keywords_matched=keywords_matched,
        missing_keywords=missing,
        reason="; ".join(reasons),
    )


def score_run(
    results: list[tuple[ExpectedOutcome, FinalSummary, list[LogLine]]],
) -> ScoreReport:
    """Score a whole eval run."""
    return ScoreReport(cases=[score_case(e, s, lines) for e, s, lines in results])