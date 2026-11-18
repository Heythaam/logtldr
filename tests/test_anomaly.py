"""Tests for anomaly detection (LT-11)."""

from logtldr.anomaly_detector import detect_anomalies
from logtldr.models import LogLine, Template


def _make_line(line_id: int, level: str = "INFO", message: str = "msg") -> LogLine:
    return LogLine(
        line_id=line_id,
        original_text=message,
        redacted_text=message,
        level=level,
        message=message,
        source_format="plaintext",
        timestamp=None,
    )


def test_first_error() -> None:
    """The first WARN/ERROR/FATAL line should be flagged."""
    lines = [
        _make_line(1, level="INFO"),
        _make_line(2, level="WARN"),
        _make_line(3, level="ERROR"),
    ]
    templates = []
    assignments = {}

    anomalies = detect_anomalies(lines, templates, assignments)
    
    assert len(anomalies) == 1
    assert anomalies[0].type == "first_error"
    assert anomalies[0].line_ids == [2]


def test_new_template() -> None:
    """Test rare error templates and late-appearing error templates."""
    # 60 lines total. Midpoint is line 30.
    lines = [_make_line(i) for i in range(1, 61)]
    
    # Make lines 11-12 ERRORs so t2 is an error template
    for i in range(11, 13):
        lines[i-1] = _make_line(i, level="ERROR")

    # Make lines 50-60 ERRORs so t3 is an error template
    for i in range(50, 61):
        lines[i-1] = _make_line(i, level="ERROR")
    
    templates = [
        # t1: Appears 10 times at the beginning. INFO. Should NOT be flagged.
        Template(template_id="t1", pattern="early info", count=10, first_occurrence_line_id=1, last_occurrence_line_id=10),
        # t2: Appears 2 times at the beginning. ERROR. Flagged for being rare (<3).
        Template(template_id="t2", pattern="rare error", count=2, first_occurrence_line_id=11, last_occurrence_line_id=12),
        # t3: Appears 10 times, but starts at line 50 (late). ERROR. Flagged for being late.
        Template(template_id="t3", pattern="late error", count=10, first_occurrence_line_id=50, last_occurrence_line_id=60),
    ]
    
    assignments = {}
    # Assign t1 (lines 1-10)
    for i in range(1, 11):
        assignments[i] = "t1"
    # Assign t2 (lines 11-12)
    for i in range(11, 13):
        assignments[i] = "t2"
    # Assign t3 (lines 50-60)
    for i in range(50, 61):
        assignments[i] = "t3"

    anomalies = detect_anomalies(lines, templates, assignments)
    
    # We expect 2 new_template anomalies (t2 and t3)
    new_templates = [a for a in anomalies if a.type == "new_template"]
    assert len(new_templates) == 2
    
    # Verify t2 (Rare)
    assert new_templates[0].template_ids == ["t2"]
    assert "Rare error" in new_templates[0].description
    
    # Verify t3 (Late)
    assert new_templates[1].template_ids == ["t3"]
    assert "Late-appearing" in new_templates[1].description

def test_grouped_stack_trace() -> None:
    """Consecutive stack trace lines should be grouped into one anomaly."""
    lines = [
        _make_line(1, level="INFO"),
        _make_line(2, level="ERROR", message="Traceback (most recent call last):"),
        _make_line(3, message="  File \"test.py\", line 12, in <module>"),
        _make_line(4, message="    raise ValueError()"),
        _make_line(5, level="INFO"),
    ]
    templates = []
    assignments = {}

    anomalies = detect_anomalies(lines, templates, assignments)
    
    stack_anomalies = [a for a in anomalies if a.type == "grouped_stack_trace"]
    assert len(stack_anomalies) == 1
    assert stack_anomalies[0].line_ids == [2, 3, 4]
    assert "3 lines" in stack_anomalies[0].description


def test_frequency_spike() -> None:
    """A template occurring way too often in a bucket should be flagged."""
    # 100 lines total. 10 buckets of 10 lines each.
    lines = [_make_line(i) for i in range(1, 101)]
    
    # t1 only appears in Bucket 1 (lines 1-10). It's 10 occurrences in one bucket!
    # The average across all 10 buckets is 10 / 10 = 1.
    # 10 is > 5x the average, so it will trigger a spike.
    assignments = {}
    for i in range(1, 11):
        lines[i-1] = _make_line(i, level="ERROR") # Must be ERROR to pass filter
        assignments[i] = "t1"
        
    # t2 appears evenly across the remaining 90 lines.
    for i in range(11, 101):
        assignments[i] = "t2"

    templates = [
        Template(template_id="t1", pattern="spike", count=10, first_occurrence_line_id=1, last_occurrence_line_id=10),
        Template(template_id="t2", pattern="normal", count=90, first_occurrence_line_id=11, last_occurrence_line_id=100),
    ]

    anomalies = detect_anomalies(lines, templates, assignments)
    
    spikes = [a for a in anomalies if a.type == "frequency_spike"]
    assert len(spikes) == 1
    assert spikes[0].template_ids == ["t1"]
    assert "10 occurrences" in spikes[0].description