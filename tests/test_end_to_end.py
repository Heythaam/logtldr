"""End-to-end pipeline tests with a mocked LLM (LT-45, LT-44)."""

import io
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from logtldr.main import main
from logtldr.prompt import SYSTEM_PROMPT

EVAL_CASES = Path(__file__).resolve().parents[1] / "eval" / "cases"

FAILING_LOG = "\n".join(
    [f"2024-01-15T10:00:00Z INFO line {i}" for i in range(54)]
    + ["2024-01-15T10:00:00Z ERROR boom"]
)
HEALTHY_LOG = "\n".join(
    f"2024-01-15T10:{i // 60:02d}:{i % 60:02d}Z INFO heartbeat ok" for i in range(60)
)


def _mock_reply(payload: dict) -> MagicMock:
    reply = MagicMock()
    reply.choices = [MagicMock()]
    reply.choices[0].message.content = json.dumps(payload)
    return reply


VALID_ANSWER = {
    "status": "ok",
    "verdict": "Mocked LLM response",
    "hypotheses": [{
        "cause": "Mocked cause",
        "evidence_type": "direct",
        "confidence": "high",
        "citations": [{"line_id": 55, "snippet": "ERROR boom"}],
    }],
}


def _run(monkeypatch: pytest.MonkeyPatch, stdin: str, argv: list[str]) -> int:
    monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
    return main(argv)


@patch("litellm.completion")
def test_skeleton_runs_end_to_end(
    mock_completion: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The pipeline runs from ingest to render and outputs valid JSON."""
    mock_completion.return_value = _mock_reply(VALID_ANSWER)

    assert _run(monkeypatch, FAILING_LOG, ["--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "ok"
    assert "Mocked cause" in payload["verdict"]
    assert payload["hypotheses"][0]["citations"][0]["line_id"] == 55


@patch("litellm.completion")
def test_real_prompt_reaches_llm(
    mock_completion: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The LLM receives the prompt builder's messages, not the old stub string."""
    mock_completion.return_value = _mock_reply(VALID_ANSWER)

    assert _run(monkeypatch, FAILING_LOG, ["--json"]) == 0

    messages = mock_completion.call_args.kwargs["messages"]
    assert messages[0] == {"role": "system", "content": SYSTEM_PROMPT}
    assert "ERROR boom" in messages[1]["content"]


@patch("litellm.completion")
def test_invalid_citation_downgrades_to_not_enough_signal(
    mock_completion: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A cause the model cannot cite verbatim does not survive the validator."""
    answer = json.loads(json.dumps(VALID_ANSWER))
    answer["hypotheses"][0]["citations"][0]["snippet"] = "disk full"  # not in line 55
    mock_completion.return_value = _mock_reply(answer)

    assert _run(monkeypatch, FAILING_LOG, ["--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "not_enough_signal"


@patch("litellm.completion")
def test_healthy_log_skips_llm(
    mock_completion: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A log with no anomalies is answered by the refuse gate; the LLM is never called."""
    assert _run(monkeypatch, HEALTHY_LOG, ["--json"]) == 0

    assert json.loads(capsys.readouterr().out)["status"] == "nothing_unusual"
    mock_completion.assert_not_called()


@pytest.mark.skipif(
    not (EVAL_CASES / "09_healthy_ci_run" / "input.log").exists(),
    reason="eval case 09 not present",
)
@patch("litellm.completion")
def test_healthy_eval_case_skips_llm(
    mock_completion: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Acceptance: eval case 09 returns nothing_unusual without calling the LLM."""
    assert main(["--json", str(EVAL_CASES / "09_healthy_ci_run" / "input.log")]) == 0

    assert json.loads(capsys.readouterr().out)["status"] == "nothing_unusual"
    mock_completion.assert_not_called()


@patch("litellm.completion")
def test_llm_failure_exits_1_with_message(
    mock_completion: MagicMock,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Network/auth errors become a one-line error and exit code 1, not a traceback."""
    mock_completion.side_effect = Exception("401 invalid api key")

    assert _run(monkeypatch, FAILING_LOG, ["--json"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "LLM step failed" in captured.err
    assert "401 invalid api key" in captured.err