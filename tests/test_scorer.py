"""Tests for the eval scorer (LT-21)."""

from eval.scorer import ExpectedOutcome, score_case, score_run
from logtldr.models import Citation, FinalSummary, Hypothesis, LogLine


def _line(line_id: int, text: str, level: str = "INFO") -> LogLine:
    return LogLine(
        line_id=line_id, original_text=text, redacted_text=text,
        level=level, message=text, source_format="plaintext",
    )


LINES = [
    _line(1, "Starting build step 3/7"),
    _line(2, "ERROR: write failed: No space left on device", "ERROR"),
    _line(3, "Process exited with code 1", "ERROR"),
]

DISK_FULL = ExpectedOutcome(
    case_id="08_github_actions_disk_full",
    expected_status="ok",
    cause_keywords=["disk", "space"],
    evidence_line_ids=[2, 3],
)
HEALTHY = ExpectedOutcome(case_id="09_healthy_ci_run", expected_status="nothing_unusual")
UNKNOWN = ExpectedOutcome(case_id="unknown_case", expected_status="not_enough_signal")


def _summary(cause: str, citations: list[Citation], status: str = "ok") -> FinalSummary:
    return FinalSummary(
        status=status,
        hypotheses=[Hypothesis(
            cause=cause, evidence_type="direct", confidence="high", citations=citations,
        )],
    )


def test_correct_with_valid_citations() -> None:
    summary = _summary(
        "Runner disk ran out of Space during build",
        [Citation(line_id=2, snippet="No space left on device")],
    )
    score = score_case(DISK_FULL, summary, LINES)
    assert score.correct
    assert score.evidence_overlap and score.citations_valid
    assert score.reason == ""


def test_wrong_evidence_lines() -> None:
    """LLM cites line 1 (a benign log line) instead of the error lines."""
    summary = _summary(
        "Network timeout contacting registry",
        [Citation(line_id=1, snippet="Starting build step 3/7")],
    )
    score = score_case(DISK_FULL, summary, LINES)
    assert not score.correct
    assert not score.evidence_overlap
    assert score.citations_valid  # citation itself is valid (line exists, snippet matches)
    assert not score.keywords_matched


def test_keywords_are_soft_signal() -> None:
    """Wrong keywords but correct evidence lines = still correct."""
    summary = _summary(
        "Build process terminated unexpectedly",  # no "disk" or "space"
        [Citation(line_id=2, snippet="No space left on device")],
    )
    score = score_case(DISK_FULL, summary, LINES)
    assert score.correct  # evidence overlap + valid citations = correct
    assert score.evidence_overlap
    assert not score.keywords_matched  # keywords miss, but that's ok
    assert set(score.missing_keywords) == {"disk"}  # "space" found in citation snippet
    assert "soft:" in score.reason  # keywords reported as soft signal


def test_false_positive_on_healthy() -> None:
    summary = _summary(
        "Build failed due to disk space",
        [Citation(line_id=1, snippet="Starting build")],
    )
    score = score_case(HEALTHY, summary, LINES)
    assert score.false_positive
    assert not score.correct
    report = score_run([(HEALTHY, summary, LINES)])
    assert report.false_positives == 1


def test_healthy_correct_is_not_false_positive() -> None:
    score = score_case(HEALTHY, FinalSummary(status="nothing_unusual"), LINES)
    assert score.correct and not score.false_positive


def test_right_evidence_but_invalid_citation_is_incorrect() -> None:
    summary = _summary(
        "disk space exhausted",
        [
            Citation(line_id=2, snippet="No space left on device"),
            Citation(line_id=3, snippet="disk quota exceeded"),  # not in line 3
        ],
    )
    score = score_case(DISK_FULL, summary, LINES)
    assert score.evidence_overlap
    assert not score.citations_valid
    assert not score.correct


def test_citation_flag_is_not_trusted() -> None:
    """A citation marked valid but pointing at a missing line still fails."""
    summary = _summary(
        "disk space exhausted",
        [Citation(line_id=99, snippet="No space left", citation_valid=True)],
    )
    assert not score_case(DISK_FULL, summary, LINES).correct


def test_no_citations_is_incorrect() -> None:
    assert not score_case(DISK_FULL, _summary("disk space exhausted", []), LINES).correct


def test_refusal_on_diagnosis_case_is_incorrect() -> None:
    score = score_case(DISK_FULL, FinalSummary(status="not_enough_signal"), LINES)
    assert not score.correct


def test_unknowns_scored_separately() -> None:
    good = _summary(
        "disk space exhausted", [Citation(line_id=2, snippet="No space left on device")]
    )
    report = score_run([
        (DISK_FULL, good, LINES),
        (UNKNOWN, FinalSummary(status="not_enough_signal"), LINES),
        (UNKNOWN, good, LINES),  # fabricated
    ])
    assert report.accuracy == 1.0  # unknowns excluded
    assert report.unknown_total == 2
    assert report.unknown_correct == 1
    assert report.fabricated == 1


def test_no_evidence_line_ids_skips_overlap_check() -> None:
    """When ground truth has no evidence_line_ids, evidence check passes."""
    no_evidence = ExpectedOutcome(
        case_id="legacy_case",
        expected_status="ok",
        cause_keywords=["disk"],
    )
    summary = _summary(
        "disk failure",
        [Citation(line_id=2, snippet="No space left on device")],
    )
    score = score_case(no_evidence, summary, LINES)
    assert score.correct  # no evidence_line_ids = skip overlap check
    assert score.evidence_overlap  # treated as pass