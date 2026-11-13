"""Log parsing: convert RawLogLine[] into LogLine[]."""

import json
import re
from datetime import datetime, timezone
from typing import Optional

from logtldr.models import LogLine, RawLogLine

_LEVEL_ALIASES = {
    "debug": "DEBUG",
    "trace": "DEBUG",
    "info": "INFO",
    "information": "INFO",
    "warn": "WARN",
    "warning": "WARN",
    "error": "ERROR",
    "err": "ERROR",
    "fatal": "FATAL",
    "critical": "FATAL",
    "crit": "FATAL",
}

_DEFAULT_LEVEL = "INFO"

_ISO8601_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?"
    r"(?:Z|[+-]\d{2}:?\d{2})?)(?:\s+|$)"
)

_SYSLOG_RE = re.compile(
    r"^(?P<ts>[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2})"
    r"(?:\s+(?:\S+\s+)?\S+:)?\s*"
)

_LEVEL_TOKEN_RE = re.compile(
    r"(?:^|\s|\[)"
    r"(?P<level>DEBUG|INFORMATION|INFO|WARNING|WARN|ERROR|ERR|FATAL|CRITICAL|CRIT|TRACE)\b",
    re.IGNORECASE,
)

_MSG_KEYS = ("message", "msg", "text", "body")
_LEVEL_KEYS = ("level", "severity", "loglevel", "lvl")
_TS_KEYS = ("timestamp", "time", "ts", "@timestamp")


def parse(raw_lines: list[RawLogLine]) -> list[LogLine]:
    """Parse a batch of raw lines into LogLine objects."""
    result: list[LogLine] = []
    for idx, raw in enumerate(raw_lines, start=1):
        if raw.format == "json":
            result.append(_parse_json(idx, raw))
        elif raw.format == "plaintext":
            result.append(_parse_plaintext(idx, raw))
        elif raw.format == "pytest":
            result.append(_parse_pytest(idx, raw))
        elif raw.format == "docker":
            result.append(_parse_docker(idx, raw))
        elif raw.format == "npm":
            result.append(_parse_npm(idx, raw))
        else:
            result.append(_parse_fallback(idx, raw))
    return result


def _parse_fallback(line_id: int, raw: RawLogLine) -> LogLine:
    return LogLine(
        line_id=line_id,
        original_text=raw.raw_text,
        redacted_text=raw.raw_text,
        timestamp=None,
        level=_DEFAULT_LEVEL,
        message=raw.raw_text,
        source_format=raw.format,
    )


def _parse_json(line_id: int, raw: RawLogLine) -> LogLine:
    try:
        obj = json.loads(raw.raw_text)
    except (json.JSONDecodeError, ValueError):
        return _parse_plaintext(line_id, raw)
    if not isinstance(obj, dict):
        return _parse_plaintext(line_id, raw)

    message = _pick(obj, _MSG_KEYS)
    if message is None:
        message = raw.raw_text
    elif not isinstance(message, str):
        message = str(message)

    return LogLine(
        line_id=line_id,
        original_text=raw.raw_text,
        redacted_text=raw.raw_text,
        timestamp=_parse_timestamp(_pick(obj, _TS_KEYS)),
        level=_normalize_level(_pick(obj, _LEVEL_KEYS)),
        message=message,
        source_format="json",
    )


def _parse_plaintext(line_id: int, raw: RawLogLine) -> LogLine:
    text = raw.raw_text
    timestamp: Optional[datetime] = None
    rest = text

    m = _ISO8601_RE.match(text)
    if m:
        timestamp = _parse_timestamp_str(m.group("ts"))
        rest = text[m.end():]
    else:
        m = _SYSLOG_RE.match(text)
        if m:
            timestamp = _parse_timestamp_str(m.group("ts"))
            rest = text[m.end():]

    level = _DEFAULT_LEVEL
    lm = _LEVEL_TOKEN_RE.search(rest)
    if lm:
        level = _normalize_level(lm.group("level"))
        prefix = rest[: lm.start()].strip()
        if prefix in ("", "["):
            end = lm.end()
            if end < len(rest) and rest[end] == "]":
                end += 1
            rest = rest[end:]

    return LogLine(
        line_id=line_id,
        original_text=text,
        redacted_text=text,
        timestamp=timestamp,
        level=level,
        message=rest.strip() or text,
        source_format="plaintext",
    )


def _parse_pytest(line_id: int, raw: RawLogLine) -> LogLine:
    text = raw.raw_text
    level = _DEFAULT_LEVEL
    if "FAILED" in text or "ERROR" in text or "AssertionError" in text or "E   " in text:
        level = "ERROR"
    elif "PASSED" in text:
        level = "INFO"
    
    m = _ISO8601_RE.match(text)
    timestamp = _parse_timestamp_str(m.group("ts")) if m else None
    
    return LogLine(
        line_id=line_id,
        original_text=text,
        redacted_text=text,
        timestamp=timestamp,
        level=level,
        message=text,
        source_format="pytest",
    )


def _parse_docker(line_id: int, raw: RawLogLine) -> LogLine:
    text = raw.raw_text
    level = _DEFAULT_LEVEL
    if "ERROR" in text or "Killed" in text or "failed" in text.lower() or "exit code: 137" in text:
        level = "ERROR"
    elif "WARN" in text:
        level = "WARN"
    
    m = _ISO8601_RE.match(text)
    timestamp = _parse_timestamp_str(m.group("ts")) if m else None
    
    return LogLine(
        line_id=line_id,
        original_text=text,
        redacted_text=text,
        timestamp=timestamp,
        level=level,
        message=text,
        source_format="docker",
    )


def _parse_npm(line_id: int, raw: RawLogLine) -> LogLine:
    text = raw.raw_text
    level = _DEFAULT_LEVEL
    if "npm ERR!" in text or "FAIL" in text or "Error" in text:
        level = "ERROR"
    elif "npm WARN" in text or "WARN" in text:
        level = "WARN"
    elif "PASS" in text:
        level = "INFO"
    
    m = _ISO8601_RE.match(text)
    timestamp = _parse_timestamp_str(m.group("ts")) if m else None
    
    return LogLine(
        line_id=line_id,
        original_text=text,
        redacted_text=text,
        timestamp=timestamp,
        level=level,
        message=text,
        source_format="npm",
    )


def _pick(obj: dict, keys: tuple[str, ...]) -> object:
    for k in keys:
        if k in obj and obj[k] is not None:
            return obj[k]
    return None


def _normalize_level(value: object) -> str:
    if value is None:
        return _DEFAULT_LEVEL
    if not isinstance(value, str):
        value = str(value)
    token = value.strip().lower()
    if token in _LEVEL_ALIASES:
        return _LEVEL_ALIASES[token]
    return token.upper() if token else _DEFAULT_LEVEL


def _parse_timestamp(value: object) -> Optional[datetime]:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        return _parse_timestamp_str(value)
    return None


def _parse_timestamp_str(s: str) -> Optional[datetime]:
    s = s.strip()
    if not s:
        return None
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    s = _trim_fraction(s)
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    
    normalized = re.sub(r"\s+", " ", s)
    m = re.match(r"^([A-Z][a-z]{2}) (\d{1,2}) (\d{2}:\d{2}:\d{2})$", normalized)
    if m:
        try:
            return datetime.strptime(
                f"{m.group(1)} {m.group(2)} {datetime.now().year} {m.group(3)}",
                "%b %d %Y %H:%M:%S",
            )
        except ValueError:
            pass
            
    for fmt in (
        "%b %d %Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(normalized, fmt)
        except ValueError:
            continue
    return None


def _trim_fraction(s: str) -> str:
    m = re.match(r"^([^.]+\.\d{6})\d+(.*)$", s)
    if m:
        return m.group(1) + m.group(2)
    return s