"""Langfuse tracing is opt-in (LT-47).

Proves:
  - Without LOGTLDR_TRACING=1, no Langfuse callback is set (cloud or local)
  - A Langfuse callback left over in the process is removed; others are kept
  - With the flag, cloud mode turns tracing on and says so on stderr
  - The flag is ignored in local mode
  - The flag without the optional extra installed warns and stays off
  - langfuse is only an optional dependency (the "tracing" extra)
"""

from importlib.metadata import requires

import litellm
import pytest

from logtldr import llm_gateway
from logtldr.config import Config
from logtldr.llm_gateway import LLMGateway


def _cloud() -> LLMGateway:
    return LLMGateway(Config(model="openai/gpt-4o-mini", api_base=None, local=False, api_key="test"))


def _local() -> LLMGateway:
    # api_key set so the Ollama reachability check is skipped
    return LLMGateway(Config(model="ollama/qwen2.5:7b", api_base="http://localhost:11434",
                             local=True, api_key="test"))


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    """No flag, empty callbacks, langfuse 'installed' unless a test says otherwise."""
    monkeypatch.delenv(llm_gateway.TRACING_ENV, raising=False)
    monkeypatch.setattr(litellm, "success_callback", [])
    monkeypatch.setattr(litellm, "failure_callback", [])
    monkeypatch.setattr(llm_gateway, "_langfuse_installed", lambda: True)


def _has_langfuse() -> bool:
    return "langfuse" in litellm.success_callback or "langfuse" in litellm.failure_callback


def test_no_flag_cloud_no_callback() -> None:
    """The bug: cloud mode used to set Langfuse callbacks unconditionally."""
    gateway = _cloud()

    assert gateway.tracing is False
    assert not _has_langfuse()


def test_no_flag_local_no_callback() -> None:
    gateway = _local()

    assert gateway.tracing is False
    assert not _has_langfuse()


def test_leftover_langfuse_removed_other_callbacks_kept() -> None:
    """Callbacks are process-wide: drop a stale 'langfuse', keep everything else."""
    litellm.success_callback = ["langfuse", "custom_logger"]
    litellm.failure_callback = ["langfuse"]

    _cloud()

    assert litellm.success_callback == ["custom_logger"]
    assert litellm.failure_callback == []


@pytest.mark.parametrize("value", ["1", "true", "YES"])
def test_flag_enables_tracing_in_cloud_mode(
    value: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(llm_gateway.TRACING_ENV, value)

    gateway = _cloud()

    assert gateway.tracing is True
    assert litellm.success_callback == ["langfuse"]
    assert litellm.failure_callback == ["langfuse"]
    assert "sent to Langfuse" in capsys.readouterr().err


def test_flag_enabled_twice_does_not_duplicate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(llm_gateway.TRACING_ENV, "1")

    _cloud()
    _cloud()

    assert litellm.success_callback == ["langfuse"]


@pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe"])
def test_other_flag_values_do_not_enable(value: str, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(llm_gateway.TRACING_ENV, value)

    assert _cloud().tracing is False
    assert not _has_langfuse()


def test_flag_ignored_in_local_mode(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Local mode promises nothing leaves the machine, flag or not."""
    monkeypatch.setenv(llm_gateway.TRACING_ENV, "1")

    gateway = _local()

    assert gateway.tracing is False
    assert not _has_langfuse()
    assert "ignored in local mode" in capsys.readouterr().err


def test_flag_without_extra_installed_warns(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(llm_gateway.TRACING_ENV, "1")
    monkeypatch.setattr(llm_gateway, "_langfuse_installed", lambda: False)

    gateway = _cloud()

    assert gateway.tracing is False
    assert not _has_langfuse()
    assert 'pip install "logtldr[tracing]"' in capsys.readouterr().err


def test_langfuse_is_only_an_optional_dependency() -> None:
    """Every langfuse requirement must be gated behind the 'tracing' extra."""
    reqs = [r for r in (requires("logtldr") or []) if r.lower().startswith("langfuse")]

    assert reqs, "langfuse should still be available via the tracing extra"
    for req in reqs:
        assert 'extra == "tracing"' in req.replace("'", '"'), req