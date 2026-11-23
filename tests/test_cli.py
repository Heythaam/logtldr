"""Deterministic CLI + models smoke tests. No network, no LLM."""

import io
import json
from unittest.mock import MagicMock, patch

import pytest

from logtldr.main import main
from logtldr.models import (
    Anomaly,
    Citation,
    CompressedView,
    FinalSummary,
    Hypothesis,
    LLMResponse,
    LogLine,
    Template,
)


def test_models_importable() -> None:
    """All eight models are importable and construct minimal instances."""
    assert LogLine(
        line_id=1,
        original_text="x",
        redacted_text="x",
        level="INFO",
        message="x",
        source_format="plaintext",
    ).line_id == 1
    assert Template(
        template_id="t1",
        pattern="<*>",
        count=1,
        first_occurrence_line_id=1,
        last_occurrence_line_id=1,
    ).count == 1
    assert Anomaly(type="new_template", line_ids=[1], description="x").line_ids == [1]
    assert Citation(line_id=1, snippet="x").line_id == 1
    assert Hypothesis(
        cause="x", evidence_type="direct", confidence="high", citations=[]
    ).evidence_type == "direct"
    assert CompressedView(
        templates=[],
        anomalies=[],
        timeline_candidates=[],
        sample_lines=[],
        statistics={},
        metadata={},
        estimated_tokens=0,
    ).estimated_tokens == 0
    assert LLMResponse(status="ok").hypotheses is None
    assert FinalSummary(status="ok").unknowns == []


@patch("httpx.get", return_value=MagicMock(status_code=200))
@patch("litellm.completion")
def test_cli_reads_stdin(
    mock_completion: MagicMock,
    mock_httpx: MagicMock,        # ADD THIS
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`logtldr` with piped input exits 0 and reports what it read."""
    # Setup the mock to return a fake JSON response
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "status": "ok",
        "verdict": "Mocked LLM response",
        "hypotheses": [
            {
                "cause": "boom happened",
                "evidence_type": "direct",
                "confidence": "high",
                "citations": [{"line_id": 55, "snippet": "ERROR boom"}],
            }
        ],
    })
    mock_completion.return_value = mock_response

    # 54 INFO lines + 1 ERROR line = 55 lines, triggers an anomaly
    lines = [f"2024-01-15T10:00:00Z INFO line {i}" for i in range(54)]
    lines.append("2024-01-15T10:00:00Z ERROR boom")
    input_text = "\n".join(lines)

    monkeypatch.setattr("sys.stdin", io.StringIO(input_text))
    rc = main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Evidence points to boom happened, confidence: medium" in out


@patch("litellm.completion")
def test_cli_json_mode(
    mock_completion: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`--json` emits parseable JSON with status 'ok'."""
    # Setup the mock with a valid hypothesis and citation
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = json.dumps({
        "status": "ok",
        "verdict": "Mocked LLM response",
        "hypotheses": [
            {
                "cause": "Mocked cause",
                "evidence_type": "direct",
                "confidence": "high",
                "citations": [{"line_id": 55, "snippet": "ERROR boom"}]
            }
        ]
    })
    mock_completion.return_value = mock_response

    lines = [f"2024-01-15T10:00:00Z INFO line {i}" for i in range(54)]
    lines.append("2024-01-15T10:00:00Z ERROR boom")
    input_text = "\n".join(lines)
    
    monkeypatch.setattr("sys.stdin", io.StringIO(input_text))
    rc = main(["--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"


def test_unknown_flag_exits_1() -> None:
    """Unknown flags raise SystemExit(1), not argparse's default 2."""
    with pytest.raises(SystemExit) as exc_info:
        main(["--unknown-flag"])
    assert exc_info.value.code == 1


def test_missing_file_returns_1(tmp_path) -> None:
    """Unreadable file path returns exit code 1."""
    rc = main([str(tmp_path / "does-not-exist.log")])
    assert rc == 1