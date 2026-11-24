"""Tests for LLM Gateway configuration (LT-16)."""

from unittest.mock import MagicMock, patch

import httpx
import pytest

from logtldr.config import Config
from logtldr.llm_gateway import LLMGateway
from logtldr.models import CompressedView, LLMResponse


def test_local_rejects_non_localhost() -> None:
    """--local with a non-localhost api_base raises ValueError."""
    config = Config(
        model="gpt-3.5-turbo",
        api_base="https://api.openai.com/v1",
        local=True
    )
    with pytest.raises(ValueError, match="Check your config"):
        LLMGateway(config)


@patch("httpx.get", side_effect=httpx.ConnectError("refused"))
def test_local_accepts_localhost(mock_get: MagicMock) -> None:
    """local=True with api_base='http://localhost:11434' passes URL validation
    but raises when Ollama is unreachable."""
    config = Config(
        model="ollama/mistral",
        api_base="http://localhost:11434",
        local=True,
    )
    with pytest.raises(ValueError, match="Ollama not running"):
        LLMGateway(config)


def test_remote_with_api_key_succeeds() -> None:
    """External API with a key and local=False succeeds."""
    config = Config(
        model="gpt-4o-mini",
        api_base="https://api.openai.com/v1",
        local=False,
        api_key="dummy-key"
    )
    # Should not raise any errors because local=False skips checks
    gateway = LLMGateway(config)
    assert gateway.local is False

# ... (keep your existing local/remote tests) ...


@patch("litellm.completion")
def test_json_schema_success(mock_completion: MagicMock) -> None:
    """The gateway should successfully parse a valid JSON response."""
    # Mock the LLM returning perfect JSON on the first try
    mock_response = MagicMock()
    mock_response.choices = [MagicMock()]
    mock_response.choices[0].message.content = '{"status": "ok", "verdict": "All clear"}'
    mock_completion.return_value = mock_response

    config = Config(model="gpt-4o", api_base="http://localhost:11434", local=True, api_key="dummy-key")
    gateway = LLMGateway(config)
    
    view = CompressedView(
        templates=[], anomalies=[], timeline_candidates=[], sample_lines=[], 
        statistics={}, metadata={}, estimated_tokens=0
    )
    
    result = gateway.call_summarize(view, LLMResponse)
    
    assert isinstance(result, LLMResponse)
    assert result.status == "ok"
    assert result.verdict == "All clear"
    assert mock_completion.call_count == 1


@patch("litellm.completion")
def test_retry_on_invalid_json(mock_completion: MagicMock) -> None:
    """The gateway should retry if the first response is invalid JSON."""
    # Mock the LLM returning invalid JSON, then valid JSON
    mock_bad = MagicMock()
    mock_bad.choices = [MagicMock()]
    mock_bad.choices[0].message.content = '{"status": "ok", verdict: missing_quotes}'  # Invalid JSON

    mock_good = MagicMock()
    mock_good.choices = [MagicMock()]
    mock_good.choices[0].message.content = '{"status": "ok", "verdict": "Fixed on retry"}'

    mock_completion.side_effect = [mock_bad, mock_good]

    config = Config(model="gpt-4o", api_base="http://localhost:11434", local=True, api_key="dummy-key")
    gateway = LLMGateway(config)
    
    view = CompressedView(
        templates=[], anomalies=[], timeline_candidates=[], sample_lines=[], 
        statistics={}, metadata={}, estimated_tokens=0
    )
    
    result = gateway.call_summarize(view, LLMResponse)
    
    assert result.verdict == "Fixed on retry"
    assert mock_completion.call_count == 2  # It tried twice


@patch("litellm.completion")
def test_max_retries_exceeded(mock_completion: MagicMock) -> None:
    """After 3 failures, it should raise a RuntimeError with a clear message."""
    # Mock the LLM always returning garbage
    mock_bad = MagicMock()
    mock_bad.choices = [MagicMock()]
    mock_bad.choices[0].message.content = 'I am not JSON at all.'
    
    mock_completion.return_value = mock_bad

    config = Config(model="gpt-4o", api_base="http://localhost:11434", local=True, api_key="dummy-key")
    gateway = LLMGateway(config)
    
    view = CompressedView(
        templates=[], anomalies=[], timeline_candidates=[], sample_lines=[], 
        statistics={}, metadata={}, estimated_tokens=0
    )
    
    with pytest.raises(RuntimeError, match="LLM returned invalid JSON after 3 attempts"):
        gateway.call_summarize(view, LLMResponse)
        
    assert mock_completion.call_count == 3  # It tried exactly 3 times



def test_langfuse_disabled_in_local_mode() -> None:
    """Local mode should not initialize Langfuse or set LiteLLM callbacks."""
    import litellm

    from logtldr.config import Config
    from logtldr.llm_gateway import LLMGateway
    
    # Reset callbacks to a known state
    litellm.success_callback = []
    litellm.failure_callback = []

    # Add api_key to bypass the Ollama check
    config = Config(model="ollama/qwen2.5:7b", api_base="http://localhost:11434", local=True, api_key="dummy-key")
    gateway = LLMGateway(config)
    
    assert gateway.local is True
    assert litellm.success_callback == []
    assert litellm.failure_callback == []