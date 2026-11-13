"""logtldr command-line entry point."""

import argparse
import sys
from pathlib import Path
from typing import NoReturn, Optional

from logtldr.models import FinalSummary


class _Parser(argparse.ArgumentParser):
    """ArgumentParser that exits with status 1 on argument errors.

    The stock ArgumentParser exits 2 on unknown flags. The CLI contract for
    logtldr is "exit 1 on any error", so we override here.
    """

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


def _read_input(source: Optional[str]) -> Optional[str]:
    """Return the log text, or None if the source cannot be read."""
    if source is None or source == "-":
        return sys.stdin.read()
    try:
        return Path(source).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"logtldr: cannot read {source}: {exc}", file=sys.stderr)
        return None


def main(argv: Optional[list[str]] = None) -> int:
    """CLI entry point. Returns the process exit code."""
    parser = _build_parser()
    args = parser.parse_args(argv)

    # No file argument and no piped input — show help instead of blocking.
    if args.source is None and sys.stdin.isatty():
        parser.print_help(sys.stderr)
        return 1

    raw = _read_input(args.source)
    if raw is None:
        return 1
    if not raw.strip():
        print("logtldr: no input on stdin or file", file=sys.stderr)
        return 1

    # Downstream pipeline is not wired yet; report what we saw so the CLI
    # is exercisable end-to-end.
    line_count = len(raw.splitlines())
    summary = FinalSummary(
        status="ok",
        verdict=f"read {line_count} log line(s) (pipeline not yet wired)",
    )
    if args.as_json:
        print(summary.model_dump_json())
    else:
        print(summary.verdict)
    return 0


if __name__ == "__main__":
    sys.exit(main())