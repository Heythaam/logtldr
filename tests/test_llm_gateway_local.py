"""Network-blocked tests for LLM Gateway (LT-39).

Uses pytest-socket to prove that:
  (a) --local rejects non-localhost immediately (no network attempt)
  (b) --local works with mocked Ollama when external sockets are blocked
"""

from unittest.mock import MagicMock, patch

import pytest

from logtldr.config import Config
from logtldr.llm_gateway import LLMGateway
from logtldr.models import CompressedView, LLMResponse


@pytest.mark.disable_socket
def test_local_rejects_non_localhost_immediately() -> None:
    """--local with a non-localhost api_base raises ValueError before any
    network call.

    With ALL sockets disabled, the fact that this raises ValueError (not a
    SocketBlockedError) proves the rejection happens at URL validation, not
    at connection time.
    """
    config = Config(
        model="ollama/llama3.1:8b",
        api_base="https://api.openai.com/v1",
        local=True,
    )
    with pytest.raises(ValueError, match="Check your config"):
        LLMGateway(config)


@pytest.mark.disable_socket
@patch("litellm.completion")
@patch("httpx.get")
def test_local_with_blocked_network_succeeds(
    mock_httpx_get: MagicMock,
    mock_completion: MagicMock,
) -> None:
    """--local with mocked Ollama succeeds even with all sockets blocked.

    Proves the gateway never opens a real socket when properly mocked —
    no loopback or external connections escape.
    """
    # Mock Ollama health-check (GET /api/tags)
    mock_httpx_get.return_value = MagicMock(status_code=200)

    # Mock LLM response
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = (
        '{"status": "ok", "verdict": "Network-blocked test passed"}'
    )
    mock_completion.return_value = mock_response

    config = Config(
        model="ollama/llama3.1:8b",
        api_base="http://localhost:11434",
        local=True,
    )
    gateway = LLMGateway(config)

    view = CompressedView(
        templates=[],
        anomalies=[],
        timeline_candidates=[],
        sample_lines=[],
        statistics={},
        metadata={},
        estimated_tokens=0,
    )

    result = gateway.call_summarize(view, LLMResponse)

    assert isinstance(result, LLMResponse)
    assert result.status == "ok"
    assert result.verdict == "Network-blocked test passed"
    mock_completion.assert_called_once()