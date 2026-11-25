"""Tests for the compressed view builder (LT-24)."""

from datetime import datetime

from logtldr.compressed_view import build_compressed_view
from logtldr.models import Anomaly, LogLine, Template


def _make_line(line_id: int, text: str) -> LogLine:
    return LogLine(
        line_id=line_id,
        original_text=text,
        redacted_text=text,
        level="INFO",
        message=text,
        source_format="plaintext",
        timestamp=datetime.now(),
    )


def test_deterministic_sampling() -> None:
    """Same input must produce the exact same output."""
    lines = [_make_line(i, f"line {i}") for i in range(1, 101)]
    templates = [Template(template_id="1", pattern="line <NUM>", count=100, first_occurrence_line_id=1, last_occurrence_line_id=100)]
    anomalies = [Anomaly(type="first_error", line_ids=[50], description="error")]

    view1 = build_compressed_view(lines, templates, anomalies)
    view2 = build_compressed_view(lines, templates, anomalies)

    assert view1.model_dump() == view2.model_dump()
    
    # Check anomaly ±5 logic (lines 45-55 should be sampled)
    sampled_ids = [line["line_id"] for line in view1.sample_lines]
    assert 45 in sampled_ids
    assert 50 in sampled_ids
    assert 55 in sampled_ids
    assert 1 in sampled_ids  # Template exemplar


def test_token_estimation_accuracy() -> None:
    """Estimated tokens must be within 5% of the actual count."""
    lines = [_make_line(i, f"line {i}") for i in range(1, 101)]
    templates = [Template(template_id="1", pattern="line <NUM>", count=100, first_occurrence_line_id=1, last_occurrence_line_id=100)]
    anomalies = []

    view = build_compressed_view(lines, templates, anomalies)
    
    # Recompute actual tokens with the same (offline-capable) tokenizer
    from logtldr.compressed_view import _get_encoding
    actual_tokens = len(_get_encoding().encode(view.model_dump_json(exclude={"estimated_tokens"})))
    
    diff = abs(view.estimated_tokens - actual_tokens)
    assert diff / actual_tokens < 0.05


def test_token_budget() -> None:
    """No CompressedView may exceed 8000 tokens."""
    # Create 5000 lines to force a massive view
    lines = [_make_line(i, f"This is a somewhat long log line number {i} with extra text to increase token count.") for i in range(1, 5001)]
    
    templates = []
    anomalies = []
    
    # Make many templates and anomalies to force lots of sampling
    for i in range(1, 5001, 10):
        templates.append(Template(template_id=str(i), pattern=f"pattern {i}", count=10, first_occurrence_line_id=i, last_occurrence_line_id=i+9))
        anomalies.append(Anomaly(type="new_template", line_ids=[i], description="x"))

    view = build_compressed_view(lines, templates, anomalies)
    
    assert view.estimated_tokens <= 8000
    assert view.truncation_note is not None