"""logtldr command-line entry point."""

import argparse
import json
import os
import sys
from importlib.metadata import PackageNotFoundError, version as pkg_version
from pathlib import Path
from typing import NoReturn, Optional

from logtldr.anomaly_detector import detect_anomalies
from logtldr.citation_validator import validate_citations
from logtldr.compressed_view import build_compressed_view
from logtldr.config import (
    DEFAULT_LOCAL_MODEL,
    VIEW_BUDGET_CLOUD,
    VIEW_BUDGET_LOCAL,
    Config,
)
from logtldr.drain_wrapper import template_logs
from logtldr.ingest import ingest
from logtldr.llm_gateway import LLMGateway
from logtldr.models import FinalSummary, LLMResponse
from logtldr.parse import parse
from logtldr.redaction import redact
from logtldr.refuse_gate import check_refuse_gate
from logtldr.renderer import render_json, render_terminal
from logtldr.rules_engine import apply_rules


# ── Config file ──────────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "logtldr"
CONFIG_FILE = CONFIG_DIR / "config.json"


def _load_config_file() -> dict:
    """Load saved config from ~/.config/logtldr/config.json."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_config_file(data: dict) -> None:
    """Save config to ~/.config/logtldr/config.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(data, indent=2) + "\n")


def _handle_config_cli(config_args: list[str]) -> int:
    """Handle 'logtldr config ...' commands."""
    if not config_args or config_args[0] == "show":
        cfg = _load_config_file()
        if not cfg:
            print("No config set. Use 'logtldr config set-model <name>' to get started.")
            return 0
        print(f"Config: {CONFIG_FILE}")
        if "model" in cfg:
            print(f"  model: {cfg['model']}")
        if "api_keys" in cfg:
            for provider in cfg["api_keys"]:
                print(f"  api_key: {provider} = ****")
        return 0

    elif config_args[0] == "set-model":
        if len(config_args) < 2:
            print("Usage: logtldr config set-model <model-name>", file=sys.stderr)
            return 1
        cfg = _load_config_file()
        cfg["model"] = config_args[1]
        _save_config_file(cfg)
        print(f"Model set to: {config_args[1]}")
        return 0

    elif config_args[0] == "set-key":
        if len(config_args) < 2:
            print("Usage: logtldr config set-key <provider>", file=sys.stderr)
            return 1
        provider = config_args[1]
        env_name = f"{provider.upper()}_API_KEY"
        try:
            import getpass
            key = getpass.getpass(f"Enter {env_name}: ")
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.", file=sys.stderr)
            return 1
        if not key.strip():
            print("Empty key, not saved.", file=sys.stderr)
            return 1
        cfg = _load_config_file()
        if "api_keys" not in cfg:
            cfg["api_keys"] = {}
        cfg["api_keys"][provider.lower()] = key.strip()
        _save_config_file(cfg)
        try:
            CONFIG_FILE.chmod(0o600)
        except OSError:
            pass  # Windows doesn't support chmod the same way
        print(f"API key saved for {provider}.")
        return 0

    elif config_args[0] == "clear":
        if CONFIG_FILE.exists():
            CONFIG_FILE.unlink()
            print("Config cleared.")
        else:
            print("No config to clear.")
        return 0

    else:
        print(f"Unknown config command: {config_args[0]}", file=sys.stderr)
        print("Usage: logtldr config [show|set-model|set-key|clear]", file=sys.stderr)
        return 1


def _get_api_key_for_model(model: str, saved_config: dict) -> Optional[str]:
    """Look up API key from saved config or env vars for the given model."""
    provider_map = {
        "groq/": "groq",
        "anthropic/": "anthropic",
        "openai/": "openai",
        "openrouter/": "openrouter",
        "gemini/": "google",
        "xai/": "xai",
    }
    provider = None
    for prefix, prov in provider_map.items():
        if model.startswith(prefix):
            provider = prov
            break

    if not provider:
        return None

    # 1. Env var takes priority
    env_name = f"{provider.upper()}_API_KEY"
    env_key = os.environ.get(env_name)
    if env_key:
        return env_key

    # 2. Saved config
    saved_keys = saved_config.get("api_keys", {})
    return saved_keys.get(provider)


# ── CLI parser (unchanged from original — no subparsers) ────────────


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _get_version() -> str:
    try:
        return pkg_version("logtldr")
    except PackageNotFoundError:
        return "0.1.0"


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="logtldr", description="Summarize log streams with an LLM.")
    parser.add_argument("--version", action="version", version=f"logtldr {_get_version()}")
    parser.add_argument("source", nargs="?", metavar="FILE", help="Log file. Omit for stdin.")
    parser.add_argument("--local", action="store_true", help="Restrict LLM to localhost.")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Emit JSON.")
    parser.add_argument(
        "--model", default=None, metavar="NAME",
        help=f"LLM model (default: {DEFAULT_LOCAL_MODEL} with --local, gpt-4o-mini otherwise).",
    )
    parser.add_argument("--yes", action="store_true", help="Skip confirmation.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    raw_args = argv if argv is not None else sys.argv[1:]

    # Fast path: config commands bypass argparse entirely
    if raw_args and raw_args[0] == "config":
        return _handle_config_cli(raw_args[1:])

    parser = _build_parser()
    args = parser.parse_args(raw_args)

    # Load saved config
    saved_config = _load_config_file()

    if args.source is None and sys.stdin.isatty():
        parser.print_help(sys.stderr)
        return 1

    # 1. INGEST
    try:
        raw_lines = ingest(args.source)
    except OSError as exc:
        where = args.source if args.source is not None else "<stdin>"
        print(f"logtldr: cannot read {where}: {exc}", file=sys.stderr)
        return 1

    if not raw_lines:
        print("logtldr: no input", file=sys.stderr)
        return 1

    # 2. PARSE
    log_lines = parse(raw_lines)

    # 3. REDACT
    redacted_lines = redact(log_lines)

    # 4. DRAIN
    templates, assignments = template_logs(redacted_lines)

    # 5. ANOMALY DETECT
    anomalies = detect_anomalies(redacted_lines, templates, assignments)

    # 6. REFUSE GATE
    gate_status, stats = check_refuse_gate(redacted_lines, anomalies)
    if gate_status != "proceed":
        final_summary = FinalSummary(
            status=gate_status,
            verdict=f"Refused by gate. Stats: {stats}"
        )
    else:
        # 7. COMPRESS → PROMPT → LLM GATEWAY
        if args.model and args.model.startswith("ollama/"):
            is_local = True
        elif args.local:
            is_local = True
        elif args.model:
            is_local = False
        elif saved_config.get("model"):
            is_local = saved_config["model"].startswith("ollama/")
        else:
            is_local = True

        model = args.model or saved_config.get("model") or (DEFAULT_LOCAL_MODEL if is_local else "gpt-4o-mini")

        # Confirmation gate: external API needs explicit opt-in
        if not is_local:
            confirm = args.yes or os.environ.get("LOGTLDR_CONFIRM_API") == "1"
            if not confirm:
                print(
                    "logtldr: sending logs to an external API requires "
                    "--yes or LOGTLDR_CONFIRM_API=1",
                    file=sys.stderr,
                )
                return 1

        api_base = "http://localhost:11434" if is_local else None

        # Look up API key from saved config or env
        api_key = _get_api_key_for_model(model, saved_config) if not is_local else None
        config = Config(model=model, api_base=api_base, local=is_local, api_key=api_key)

        # Token budget
        view_budget = VIEW_BUDGET_LOCAL if is_local else VIEW_BUDGET_CLOUD
        compressed_view = build_compressed_view(
            redacted_lines, templates, anomalies, max_tokens=view_budget,
        )

        try:
            gateway = LLMGateway(config)
            llm_response = gateway.call_summarize(compressed_view, LLMResponse)
        except Exception as exc:  # CLI boundary: config, network, auth or bad-JSON failures
            print(f"logtldr: LLM step failed: {exc}", file=sys.stderr)
            return 1

        # 8. CITATION VALIDATE
        validated_response = validate_citations(llm_response, redacted_lines)

        # 9. RULES ENGINE
        final_summary = apply_rules(validated_response, redacted_lines)

    # 10. RENDER
    if args.as_json:
        print(render_json(final_summary))
    else:
        print(render_terminal(final_summary))

    return 0


def _entry() -> None:
    """Console-script entry point for setuptools."""
    sys.exit(main())


if __name__ == "__main__":
    _entry()