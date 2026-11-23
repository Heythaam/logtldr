"""logtldr command-line entry point."""

import argparse
import os
import sys
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


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(prog="logtldr", description="Summarize log streams with an LLM.")
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
    parser = _build_parser()
    args = parser.parse_args(argv)

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
        # Resolve model: --local without --model → baseline local model
                # Resolve local vs external ---------------------------------
        #   Local is the default.  External only when the user passes
        #   a non-ollama --model *and* confirms with --yes or env var.

        if args.model and args.model.startswith("ollama/"):
            is_local = True
        elif args.local:
            is_local = True
        elif args.model:
            # Explicit non-ollama model → external
            is_local = False
        else:
            # No model, no --local → safe default is local
            is_local = True

        model = args.model or (DEFAULT_LOCAL_MODEL if is_local else "gpt-4o-mini")

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
        config = Config(model=model, api_base=api_base, local=is_local)

        # Token budget: local models get a smaller budget so they stay
        # responsive on consumer hardware.
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


if __name__ == "__main__":
    sys.exit(main())