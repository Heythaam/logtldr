"""Tests for LLM Gateway configuration (LT-16)."""

import pytest

from logtldr.config import Config
from logtldr.llm_gateway import LLMGateway


def test_local_rejects_non_localhost() -> None:
    """--local with a non-localhost api_base raises ValueError."""
    config = Config(
        model="gpt-3.5-turbo",
        api_base="https://api.openai.com/v1",
        local=True
    )
    with pytest.raises(ValueError, match="Check your config"):
        LLMGateway(config)


def test_local_accepts_localhost() -> None:
    """local=True with api_base='http://localhost:11434' succeeds."""
    config = Config(
        model="ollama/mistral",
        api_base="http://localhost:11434",
        local=True
    )
    # We don't mock Ollama here, we just need to ensure the URL validation 
    # passes before it attempts the Ollama connection.
    with pytest.raises(ValueError, match="Ollama not running"):
        # It will raise this because we don't have Ollama running in the test env,
        # but the URL check passed successfully.
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