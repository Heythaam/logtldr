"""End-to-end walking skeleton test (LT-45)."""

import io
import json

import pytest

from logtldr.main import main


def test_skeleton_runs_end_to_end(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """The pipeline runs from ingest to render and outputs valid JSON."""
    lines = [f"2024-01-15T10:00:00Z INFO line {i}" for i in range(54)]
    lines.append("2024-01-15T10:00:00Z ERROR boom")
    input_text = "\n".join(lines)
    
    monkeypatch.setattr("sys.stdin", io.StringIO(input_text))

    # Run with --json
    rc = main(["--json"])
    assert rc == 0
    
    # Verify output
    out = capsys.readouterr().out
    payload = json.loads(out)
    
    assert payload["status"] == "ok"
    assert "Stub LLM response" in payload["verdict"]