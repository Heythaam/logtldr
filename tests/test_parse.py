"""Tests for log ingest and (future) parsing. Deterministic, no network."""

import io

import pytest

from logtldr.ingest import detect_format, ingest


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