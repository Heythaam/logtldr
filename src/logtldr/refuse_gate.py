"""Refuse-to-guess gate."""

from logtldr.models import Anomaly


# TODO(LT-GATE): Replace with <50 lines or <30% parseable logic from AD-1/AD-9.
def check_refuse_gate(anomalies: list[Anomaly]) -> str:
    """Determine if we have enough signal to proceed. Stub always proceeds."""
    return "proceed"