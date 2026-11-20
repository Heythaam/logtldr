"""Refuse-to-guess gate."""

from logtldr.models import Anomaly, LogLine

# CI formats are naturally shorter than service logs
_CI_FORMATS = ("pytest", "docker", "npm", "json")


def _is_parseable(line: LogLine) -> bool:
    """A line is parseable if we extracted a timestamp, level, or it's a known strict format."""
    if line.timestamp is not None:
        return True
    if line.source_format in ("json", "pytest", "docker", "npm"):
        return True
    if line.level != "INFO":
        return True
    if line.message != line.original_text:
        return True
    return False


def check_refuse_gate(
    lines: list[LogLine], anomalies: list[Anomaly]
) -> tuple[str, dict[str, int]]:
    """Determine if we have enough signal to proceed.
    
    Returns a tuple of (status, statistics).
    """
    total_lines = len(lines)
    
    # Calculate statistics
    parseable_lines = sum(1 for line in lines if _is_parseable(line))
    error_count = sum(1 for line in lines if line.level in ("ERROR", "FATAL"))
    warning_count = sum(1 for line in lines if line.level == "WARN")
    
    stats = {
        "total_lines": total_lines,
        "parseable_lines": parseable_lines,
        "error_count": error_count,
        "warning_count": warning_count,
    }

    # 1. Too little data (Line count)
    is_ci = any(line.source_format in _CI_FORMATS for line in lines)
    min_lines = 5 if is_ci else 50
    
    if total_lines < min_lines:
        return "not_enough_signal", stats

    # 2. Too little data (Parseable percentage)
    if total_lines > 0 and (parseable_lines / total_lines) < 0.30:
        return "not_enough_signal", stats

    # 3. Nothing unusual (No anomalies found)
    if not anomalies:
        return "nothing_unusual", stats

    return "proceed", stats