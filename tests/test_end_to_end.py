"""End-to-end walking skeleton test (LT-45)."""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from logtldr.main import main


@patch("litellm.completion")
def test_skeleton_runs_end_to_end(
    mock_completion: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The pipeline runs from ingest to render and outputs valid JSON."""
    # Setup the mock
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"status": "ok", "verdict": "Mocked LLM response"}'
    mock_completion.return_value = mock_response

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
    assert "Mocked LLM response" in payload["verdict"]