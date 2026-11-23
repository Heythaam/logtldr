"""--local enforcement hardening tests (LT-38).

Proves:
  - Local is the default when no model is specified
  - External API requires --yes or LOGTLDR_CONFIRM_API=1
  - Piped input never blocks waiting for confirmation
  - No silent fallback to an external API
"""

from unittest.mock import patch

import pytest

from logtldr.main import main


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove confirmation env var so each test starts clean."""
    monkeypatch.delenv("LOGTLDR_CONFIRM_API", raising=False)


# --------------- default-to-local ---------------

@patch("logtldr.main.check_refuse_gate", return_value=("nothing_unusual", {}))
def test_default_is_local(mock_gate, tmp_path, capsys):
    """No flags → local mode.  If is_local were False the confirmation
    gate would have printed an error and returned 1."""
    log = tmp_path / "app.log"
    log.write_text("2024-01-01 INFO started\n" * 20)

    rc = main([str(log)])

    assert mock_gate.called                        # pipeline reached refuse gate
    assert "--yes" not in capsys.readouterr().err   # no confirmation error
    assert rc != 1                                  # did not exit at gate


# --------------- confirmation gate ---------------

def test_external_model_without_yes_exits(tmp_path, capsys):
    """An explicit external model without --yes exits with error."""
    log = tmp_path / "app.log"
    log.write_text("2024-01-01 ERROR something broke\n" * 20)

    rc = main([str(log), "--model", "groq/openai/gpt-oss-120b"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "--yes" in err
    assert "LOGTLDR_CONFIRM_API" in err


@patch("logtldr.main.check_refuse_gate", return_value=("nothing_unusual", {}))
def test_external_model_with_yes_proceeds(mock_gate, tmp_path, capsys):
    """--yes lets an external model through the confirmation gate."""
    log = tmp_path / "app.log"
    log.write_text("2024-01-01 INFO started\n" * 20)

    rc = main([str(log), "--model", "groq/openai/gpt-oss-120b", "--yes"])

    assert mock_gate.called                        # got past the gate
    assert "--yes" not in capsys.readouterr().err
    assert rc != 1


@patch("logtldr.main.check_refuse_gate", return_value=("nothing_unusual", {}))
def test_env_var_confirms_external(mock_gate, tmp_path, capsys, monkeypatch):
    """LOGTLDR_CONFIRM_API=1 works like --yes."""
    monkeypatch.setenv("LOGTLDR_CONFIRM_API", "1")

    log = tmp_path / "app.log"
    log.write_text("2024-01-01 INFO started\n" * 20)

    rc = main([str(log), "--model", "groq/openai/gpt-oss-120b"])

    assert mock_gate.called
    assert "--yes" not in capsys.readouterr().err
    assert rc != 1


# --------------- piped input never blocks ---------------

def test_piped_input_no_confirmation_never_blocks(tmp_path, capsys):
    """With piped input (no tty) and an external model, exits immediately
    instead of trying to read confirmation from stdin."""
    log = tmp_path / "app.log"
    log.write_text("2024-01-01 ERROR crash\n" * 20)

    rc = main([str(log), "--model", "gpt-4o-mini"])

    assert rc == 1
    assert "--yes" in capsys.readouterr().err


# --------------- ollama prefix stays local ---------------

@patch("logtldr.main.check_refuse_gate", return_value=("nothing_unusual", {}))
def test_ollama_prefix_always_local(mock_gate, tmp_path, capsys):
    """An ollama/ model is always local, no --yes needed."""
    log = tmp_path / "app.log"
    log.write_text("2024-01-01 INFO started\n" * 20)

    rc = main([str(log), "--model", "ollama/llama3.1:8b"])

    assert mock_gate.called
    assert "--yes" not in capsys.readouterr().err
    assert rc != 1