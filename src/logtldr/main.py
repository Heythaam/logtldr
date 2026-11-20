"""logtldr command-line entry point."""

import argparse
import sys
from typing import NoReturn, Optional

from logtldr.anomaly_detector import detect_anomalies
from logtldr.citation_validator import validate_citations
from logtldr.drain_wrapper import template_logs
from logtldr.ingest import ingest
from logtldr.llm_gateway import build_compressed_view, summarize
from logtldr.models import FinalSummary
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
    parser.add_argument("--model", default="gpt-4o-mini", metavar="NAME", help="LLM model.")
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
        # 7. LLM GATEWAY
        compressed_view = build_compressed_view(redacted_lines, templates, anomalies)
        llm_response = summarize(compressed_view)

        # 8. CITATION VALIDATE
        validated_response = validate_citations(llm_response, redacted_lines)

        # 9. RULES ENGINE
        final_summary = apply_rules(validated_response)

    # 10. RENDER
    if args.as_json:
        print(render_json(final_summary))
    else:
        print(render_terminal(final_summary))

    return 0


if __name__ == "__main__":
    sys.exit(main())