"""Tests for the refuse-to-guess gate (LT-14)."""

from datetime import datetime

from logtldr.models import Anomaly, LogLine
from logtldr.refuse_gate import check_refuse_gate


def _make_line(
    line_id: int, level: str = "INFO", format: str = "plaintext", msg: str = "msg"
) -> LogLine:
    return LogLine(
        line_id=line_id,
        original_text=msg,
        redacted_text=msg,
        level=level,
        message=msg,
        source_format=format,
        timestamp=datetime.now(),  # <-- ADD THIS
    )


def test_not_enough_signal() -> None:
    """If the log is too short, it should return not_enough_signal."""
    lines = [_make_line(i) for i in range(1, 20)]  # 19 lines (< 50)
    anomalies = [Anomaly(type="x", line_ids=[1], description="desc")]
    
    status, stats = check_refuse_gate(lines, anomalies)
    
    assert status == "not_enough_signal"
    assert stats["total_lines"] == 19


def test_short_ci_log_not_refused() -> None:
    """A short CI log (e.g., 15 lines of pytest) should NOT be refused."""
    lines = [_make_line(i, format="pytest", level="ERROR") for i in range(1, 16)]
    anomalies = [Anomaly(type="x", line_ids=[1], description="desc")]
    
    status, stats = check_refuse_gate(lines, anomalies)
    
    assert status == "proceed"  # It proceeds because 15 > 5 (CI minimum)
    assert stats["error_count"] == 15


def test_nothing_unusual_no_llm() -> None:
    """If there are no anomalies, the gate should halt and return nothing_unusual."""
    lines = [_make_line(i) for i in range(1, 60)]  # 59 lines (> 50)
    anomalies = []

    status, stats = check_refuse_gate(lines, anomalies)

    assert status == "nothing_unusual"
    assert stats["total_lines"] == 59
    assert stats["parseable_lines"] == 59  # <-- Changed from 0
    assert stats["error_count"] == 0


def test_low_parseable_percentage() -> None:
    """If less than 30% of a large log is parseable, return not_enough_signal."""
    lines = []
    
    # We need unparseable lines here, so we explicitly pass timestamp=None
    def _bad_line(line_id: int) -> LogLine:
        return LogLine(
            line_id=line_id, original_text="msg", redacted_text="msg",
            level="INFO", message="msg", source_format="plaintext", timestamp=None
        )

    # 10 lines are parseable (error level)
    for i in range(1, 11):
        lines.append(_make_line(i, level="ERROR"))
        
    # 90 lines are unparseable
    for i in range(11, 101):
        lines.append(_bad_line(i))
        
    status, stats = check_refuse_gate(lines, [])
    
    assert status == "not_enough_signal"
    assert stats["parseable_lines"] == 10
    assert stats["total_lines"] == 100