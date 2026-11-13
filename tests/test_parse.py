"""Tests for log ingest and (future) parsing. Deterministic, no network."""

import io

import pytest

from logtldr.ingest import detect_format, ingest
from logtldr.models import RawLogLine  # noqa: E402
from logtldr.parse import parse  # noqa: E402


def test_ingest_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """ingest() reads from stdin when no source is given."""
    monkeypatch.setattr("sys.stdin", io.StringIO("line one\nline two\n"))
    result = ingest()
    assert [r.raw_text for r in result] == ["line one", "line two"]


def test_ingest_file(tmp_path) -> None:
    """ingest(path) reads from a file."""
    p = tmp_path / "app.log"
    p.write_text("alpha\nbeta\n", encoding="utf-8")
    result = ingest(str(p))
    assert [r.raw_text for r in result] == ["alpha", "beta"]


def test_ingest_detects_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """JSON-lines input is tagged with format 'json'."""
    payload = '{"ts": 1, "msg": "ok"}\n{"ts": 2, "msg": "ok"}\n'
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    result = ingest()
    assert result and all(r.format == "json" for r in result)


def test_ingest_detects_plaintext(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plaintext input is tagged 'plaintext'."""
    payload = "INFO starting\nINFO done\n"
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    result = ingest()
    assert result and all(r.format == "plaintext" for r in result)


def test_ingest_detects_pytest(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pytest output is tagged 'pytest'."""
    payload = (
        "============================= test session starts =============================\n"
        "collected 3 items\n"
        "tests/test_x.py::a PASSED\n"
    )
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    result = ingest()
    assert result and result[0].format == "pytest"


def test_ingest_empty_input(monkeypatch: pytest.MonkeyPatch) -> None:
    """Empty stdin returns an empty list."""
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    assert ingest() == []


def test_ingest_missing_file_raises(tmp_path) -> None:
    """Unreadable path raises OSError."""
    with pytest.raises(OSError):
        ingest(str(tmp_path / "nope.log"))


def test_ingest_detect_format_json_vs_plaintext() -> None:
    """detect_format discriminates JSON from plaintext directly."""
    assert detect_format(['{"a": 1}', '{"b": 2}']) == "json"
    assert detect_format(["INFO ok", "INFO done"]) == "plaintext"
    assert detect_format([]) == "plaintext"


# ---------- parse tests (LT-05) ----------

def test_json_lines() -> None:
    """JSON lines extract level, message, timestamp; missing fields default."""
    raws = [
        RawLogLine(
            format="json",
            raw_text='{"timestamp": "2024-01-15T10:23:45Z", "level": "INFO", "message": "service started"}',
        ),
        RawLogLine(
            format="json",
            raw_text='{"ts": 1705316625, "severity": "ERROR", "msg": "boom"}',
        ),
        RawLogLine(format="json", raw_text='{"message": "only message"}'),
    ]
    lines = parse(raws)

    assert len(lines) == 3
    assert lines[0].line_id == 1
    assert lines[0].level == "INFO"
    assert lines[0].message == "service started"
    assert lines[0].timestamp is not None
    assert lines[0].timestamp.year == 2024
    assert lines[0].source_format == "json"

    assert lines[1].level == "ERROR"
    assert lines[1].message == "boom"
    assert lines[1].timestamp is not None

    # Missing level → default INFO; missing timestamp → None
    assert lines[2].level == "INFO"
    assert lines[2].message == "only message"
    assert lines[2].timestamp is None


def test_plaintext_with_timestamps() -> None:
    """ISO8601 and syslog prefixes are extracted; level and message split."""
    raws = [
        RawLogLine(
            format="plaintext", raw_text="2024-01-15T10:23:45Z INFO service started"
        ),
        RawLogLine(
            format="plaintext", raw_text="2024-01-15 10:23:45 WARN something odd"
        ),
        RawLogLine(
            format="plaintext",
            raw_text="Jan  1 12:34:56 host app[1234]: ERROR failed",
        ),
        RawLogLine(format="plaintext", raw_text="no timestamp here"),
    ]
    lines = parse(raws)

    assert len(lines) == 4
    assert lines[0].level == "INFO"
    assert lines[0].message == "service started"
    assert lines[0].timestamp is not None
    assert lines[0].timestamp.year == 2024

    assert lines[1].level == "WARN"
    assert lines[1].message == "something odd"
    assert lines[1].timestamp is not None

    assert lines[2].level == "ERROR"
    assert lines[2].message == "failed"
    assert lines[2].timestamp is not None

    # No timestamp, no level → both defaults (None / INFO)
    assert lines[3].level == "INFO"
    assert lines[3].timestamp is None
    assert lines[3].message == "no timestamp here"


def test_parse_missing_fields() -> None:
    """Missing keys don't raise; defaults applied per AD-2."""
    raws = [
        RawLogLine(format="json", raw_text='{"level": "ERROR"}'),
        RawLogLine(format="json", raw_text="this is not json"),
        RawLogLine(format="json", raw_text='{"lvl": "warning", "msg": "odd"}'),
    ]
    lines = parse(raws)

    # JSON with only level: message falls back to raw_text
    assert lines[0].level == "ERROR"
    assert lines[0].message == '{"level": "ERROR"}'

    # Malformed JSON: falls back to plaintext parse, not dropped
    assert lines[1].source_format == "plaintext"

    # Alternative keys: lvl → level, msg → message
    assert lines[2].level == "WARN"
    assert lines[2].message == "odd"

def test_fallback() -> None:
    """Unknown formats → LogLine with message = raw text, no extraction.

    Covers: literal unknown format, custom vendor format, and formats the
    ingest stage detects but the parser doesn't yet support (pytest).
    """
    raws = [
        RawLogLine(
            format="unknown",
            raw_text="2024-01-15T10:23:45Z INFO this looks parseable but is not",
        ),
        RawLogLine(format="custom_vendor", raw_text='{"would": "parse as json"}'),
        RawLogLine(format="pytest", raw_text="tests/test_x.py::a PASSED"),
    ]
    lines = parse(raws)

    assert len(lines) == 3
    for i, (line, raw) in enumerate(zip(lines, raws), start=1):
        assert line.line_id == i
        assert line.original_text == raw.raw_text
        assert line.redacted_text == raw.raw_text
        assert line.message == raw.raw_text  # message is the full line
        assert line.timestamp is None  # no timestamp extraction
        assert line.level == "INFO"  # default, not extracted
        assert line.source_format == raw.format