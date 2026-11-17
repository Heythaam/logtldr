"""Anomaly detection (AD-5)."""

from logtldr.models import Anomaly, LogLine, Template


# TODO(LT-ANOMALY): Replace with actual spike/new-template logic from AD-5.
def detect_anomalies(
    lines: list[LogLine], templates: list[Template], assignments: dict[int, str]
) -> list[Anomaly]:
    """Detect anomalies. Stub returns an empty list."""
    return []