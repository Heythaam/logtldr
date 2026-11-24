"""Log ingestion: read from stdin or file, detect format hints."""

import json
import sys
from pathlib import Path
from typing import Optional

from logtldr.models import RawLogLine

# Made these highly specific to avoid false positives
_PYTEST_MARKERS = ("test session starts", "=== FAILURES ===", "collected ")
_NPM_MARKERS = ("npm ERR!", "npm WARN", "node_modules/")
_DOCKER_BUILD_MARKERS = (
    "Step 1/", 
    "Step 2/", 
    "Step 3/", 
    " ---> Running in", 
    " ---> Using cache", 
    "Successfully built"
)


def ingest(source: Optional[str] = None) -> list[RawLogLine]:
    """Read log lines from a file (``source``) or stdin (``None`` or ``"-"``).

    Returns one ``RawLogLine`` per input line, all tagged with the detected
    format hint. Raises ``OSError`` if ``source`` cannot be read.

    Example:
        >>> import io, sys
        >>> old = sys.stdin
        >>> sys.stdin = io.StringIO("INFO ok\\n")
        >>> [r.raw_text for r in ingest()]
        ['INFO ok']
        >>> sys.stdin = old
    """
    if source is None or source == "-":
        text = sys.stdin.read()
    else:
        text = Path(source).read_text(encoding="utf-8", errors="replace")

    lines = text.splitlines()
    fmt = detect_format(lines)
    return [RawLogLine(format=fmt, raw_text=line) for line in lines]


def detect_format(lines: list[str]) -> str:
    """Return a format hint: json, plaintext, pytest, docker, or npm."""
    non_empty = [ln for ln in lines if ln.strip()]
    if not non_empty:
        return "plaintext"
        
    if _mostly_json(non_empty):
        return "json"
        
    head = non_empty[:50]
    
    if any(any(m in ln for m in _PYTEST_MARKERS) for ln in head):
        return "pytest"
        
    if any(any(m in ln for m in _NPM_MARKERS) for ln in head):
        return "npm"
        
    if any(any(m in ln for m in _DOCKER_BUILD_MARKERS) for ln in head):
        return "docker"
        
    return "plaintext"


def _mostly_json(lines: list[str]) -> bool:
    ok = 0
    for ln in lines:
        try:
            json.loads(ln)
            ok += 1
        except (json.JSONDecodeError, ValueError):
            pass
    return ok >= max(1, int(len(lines) * 0.8))