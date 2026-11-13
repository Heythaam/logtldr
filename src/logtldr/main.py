"""logtldr command-line entry point."""

import argparse
import sys
from typing import NoReturn, Optional

from logtldr.ingest import ingest
from logtldr.models import FinalSummary
from logtldr.parse import parse


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that exits with status 1 on argument errors."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        self.exit(1, f"{self.prog}: error: {message}\n")


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="logtldr",
        description="Summarize log streams with an LLM.",
    )
    parser.add_argument(
        "source",
        nargs="?",
        metavar="FILE",
        help="Log file to read. Omit or use '-' to read from stdin.",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Restrict the LLM to localhost; error on any remote endpoint.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="as_json",
        help="Emit the FinalSummary as JSON instead of plain text.",
    )
    parser.add_argument(
        "--model",
        default="gpt-4o-mini",
        metavar="NAME",
        help="LLM model identifier passed to LiteLLM (default: %(default)s).",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Skip interactive confirmation prompts.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.source is None and sys.stdin.isatty():
        parser.print_help(sys.stderr)
        return 1

    try:
        raw_lines = ingest(args.source)
    except OSError as exc:
        where = args.source if args.source is not None else "<stdin>"
        print(f"logtldr: cannot read {where}: {exc}", file=sys.stderr)
        return 1

    if not raw_lines:
        print("logtldr: no input on stdin or file", file=sys.stderr)
        return 1

    log_lines = parse(raw_lines)
    level_counts: dict[str, int] = {}
    for ln in log_lines:
        level_counts[ln.level] = level_counts.get(ln.level, 0) + 1
    levels_str = ", ".join(f"{k}:{v}" for k, v in sorted(level_counts.items()))

    detected_format = raw_lines[0].format
    summary = FinalSummary(
        status="ok",
        verdict=(
            f"read {len(raw_lines)} log line(s), format '{detected_format}', "
            f"parsed {len(log_lines)} (levels: {levels_str}); "
            f"pipeline not yet wired"
        ),
    )
    if args.as_json:
        print(summary.model_dump_json())
    else:
        print(summary.verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())