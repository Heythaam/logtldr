"""Offline tokenizer tests (LT-46).

Proves:
  - Importing logtldr never loads the tokenizer (so it never touches the network)
  - `logtldr --version` works with all sockets blocked
  - The exact tokenizer loads offline from litellm's bundled copy
  - If no tokenizer can load, token counts fall back to an estimate
  - A full local run works with all sockets blocked
"""

import importlib
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import tiktoken
import tiktoken.registry

from logtldr import compressed_view
from logtldr.main import main
from logtldr.models import Template

CASE_LOG = Path(__file__).parent.parent / "eval" / "cases" / "06_docker_build_oom" / "input.log"


@pytest.fixture(autouse=True)
def _fresh_tokenizer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Each test starts with nothing loaded: no module cache, no tiktoken cache."""
    monkeypatch.setattr(compressed_view, "_encoding", None)
    monkeypatch.setattr(compressed_view, "_encoding_failed", False)
    monkeypatch.setattr(tiktoken.registry, "ENCODINGS", {})
    monkeypatch.delenv("TIKTOKEN_CACHE_DIR", raising=False)
    monkeypatch.delenv("LOGTLDR_CONFIRM_API", raising=False)


def test_import_does_not_load_tokenizer(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-importing the module must not call tiktoken at all."""
    def _boom(name: str) -> None:
        raise AssertionError("tokenizer loaded at import time")

    monkeypatch.setattr(tiktoken, "get_encoding", _boom)

    importlib.reload(compressed_view)  # would raise if it loaded at import

    assert compressed_view._encoding is None


@pytest.mark.disable_socket
def test_version_works_offline(capsys: pytest.CaptureFixture[str]) -> None:
    """`logtldr --version` with all sockets blocked."""
    with pytest.raises(SystemExit) as exc:
        main(["--version"])

    assert exc.value.code == 0
    assert "logtldr" in capsys.readouterr().out
    assert compressed_view._encoding is None  # --version never needs it


@pytest.mark.disable_socket
def test_exact_tokenizer_loads_offline() -> None:
    """litellm's bundled cl100k_base loads with no network and no env leak."""
    encoding = compressed_view._get_encoding()

    assert encoding is not None
    assert encoding.name == "cl100k_base"
    assert compressed_view._encoding_failed is False
    assert "TIKTOKEN_CACHE_DIR" not in os.environ  # restored after loading


def test_user_cache_dir_is_restored(monkeypatch: pytest.MonkeyPatch) -> None:
    """A user's own TIKTOKEN_CACHE_DIR is put back after loading."""
    monkeypatch.setenv("TIKTOKEN_CACHE_DIR", "/my/own/cache")

    compressed_view._get_encoding()

    assert os.environ["TIKTOKEN_CACHE_DIR"] == "/my/own/cache"


def test_fallback_estimate_when_no_tokenizer(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No tokenizer at all: counts become ~len/4 and the user is told once."""
    monkeypatch.setattr(compressed_view, "_load_encoding", lambda: None)

    assert compressed_view._count_text("a" * 40) == 10
    assert compressed_view._count_text("abc") == 1
    assert compressed_view._count_text("") == 0
    compressed_view._count_text("more text")  # second use: no second warning

    err = capsys.readouterr().err
    assert err.count("approximate token counts") == 1


def test_fallback_still_builds_view(monkeypatch: pytest.MonkeyPatch) -> None:
    """The compressed view still builds and respects the budget with the estimate."""
    from logtldr.models import LogLine

    monkeypatch.setattr(compressed_view, "_load_encoding", lambda: None)
    lines = [
        LogLine(line_id=i, original_text=f"line {i}", redacted_text=f"line {i}",
                level="INFO", message=f"line {i}", source_format="plaintext")
        for i in range(1, 2001)
    ]
    templates = [Template(template_id="1", pattern="line <NUM>", count=2000,
                          first_occurrence_line_id=1, last_occurrence_line_id=2000)]

    view = compressed_view.build_compressed_view(lines, templates, [], max_tokens=3000)

    assert 0 < view.estimated_tokens <= 3000


@pytest.mark.disable_socket
@patch("litellm.completion")
@patch("httpx.get")
def test_full_local_run_offline(
    mock_httpx_get: MagicMock,
    mock_completion: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """End to end with all sockets blocked: ingest → tokenizer → mocked Ollama → render."""
    mock_httpx_get.return_value = MagicMock(status_code=200)
    reply = MagicMock()
    reply.choices = [MagicMock()]
    reply.choices[0].message.content = '{"status": "ok", "verdict": "offline run ok"}'
    mock_completion.return_value = reply

    rc = main([str(CASE_LOG), "--local", "--json"])

    captured = capsys.readouterr()
    assert rc == 0, captured.err
    assert mock_completion.called  # pipeline reached the LLM step
    assert compressed_view._encoding is not None  # exact tokenizer was used
    assert "approximate token counts" not in captured.err