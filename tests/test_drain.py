"""Tests for drain3 template mining (LT-10)."""

from logtldr.drain_wrapper import template_logs
from logtldr.models import LogLine


def _make_line(line_id: int, message: str) -> LogLine:
    return LogLine(
        line_id=line_id,
        original_text=message,
        redacted_text=message,
        level="INFO",
        message=message,
        source_format="plaintext",
    )


def test_template_mining() -> None:
    """Lines with the same structure should collapse into one template."""
    lines = [
        _make_line(1, "User 123 logged in from 192.168.1.1"),
        _make_line(2, "User 456 logged in from 10.0.0.5"),
        _make_line(3, "User 789 logged in from 172.16.0.1"),
    ]
    templates, assignments = template_logs(lines)
    
    # 3 lines should map to exactly 1 template
    assert len(templates) == 1
    t = templates[0]
    
    assert t.count == 3
    assert t.first_occurrence_line_id == 1
    assert t.last_occurrence_line_id == 3
    
    # The numbers and IPs should be masked
    assert "<NUM>" in t.pattern
    assert "<IP>" in t.pattern
    assert "User" in t.pattern


def test_masking_uuid_and_hex() -> None:
    """UUIDs and Hex strings should be masked cleanly."""
    lines = [
        _make_line(1, "Task 0xAF12 failed with id 123e4567-e89b-12d3-a456-426614174000"),
        _make_line(2, "Task 0xBC34 failed with id 987e6543-e21b-12d3-a456-426614174111"),
    ]
    templates, assignments = template_logs(lines)
    
    assert len(templates) == 1
    pattern = templates[0].pattern
    
    assert "<HEX>" in pattern
    assert "<UUID>" in pattern
    assert "0xAF12" not in pattern
    assert "123e4567" not in pattern


def test_stable_ids() -> None:
    """Running the miner twice on the same input produces the exact same IDs."""
    lines = [
        _make_line(1, "User 1 logged in"),
        _make_line(2, "Disk full on /dev/sda1"),
        _make_line(3, "User 2 logged in"),
    ]
    
    t1, a1 = template_logs(lines)
    t2, a2 = template_logs(lines)
    
    assert len(t1) == 2
    assert len(t2) == 2
    
    # Assignments should be identical
    assert a1 == a2
    
    # Template IDs and patterns should be identical
    for template1, template2 in zip(t1, t2):
        assert template1.template_id == template2.template_id
        assert template1.pattern == template2.pattern