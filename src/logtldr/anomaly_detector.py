"""Anomaly detection (AD-5)."""

from logtldr.models import Anomaly, LogLine, Template


def detect_anomalies(
    lines: list[LogLine], templates: list[Template], assignments: dict[int, str]
) -> list[Anomaly]:
    """Detect anomalies from parsed lines and templates.

    Detects:
    - first_error: The first WARN/ERROR/FATAL line in the log.
    - new_template: A template containing an error that appears for the first time 
      after the first 50% of the window (only for logs > 50 lines), OR has a 
      total count < 3.
    """
    anomalies: list[Anomaly] = []
    if not lines:
        return anomalies

    # Map line_id -> line object for quick lookup
    line_by_id = {line.line_id: line for line in lines}
    total_lines = len(lines)
    mid_point = total_lines // 2

    # 1. Detect First Error
    for line in lines:
        if line.level in ("WARN", "ERROR", "FATAL"):
            anomalies.append(
                Anomaly(
                    type="first_error",
                    timestamp=line.timestamp,
                    line_ids=[line.line_id],
                    description=f"First {line.level} encountered: {line.message}",
                )
            )
            break

    # 2. Detect New / Rare Templates
    lines_by_template: dict[str, list[int]] = {}
    for line in lines:
        t_id = assignments.get(line.line_id)
        if t_id:
            lines_by_template.setdefault(t_id, []).append(line.line_id)

    template_by_id = {t.template_id: t for t in templates}

    for t_id, t_line_ids in lines_by_template.items():
        template = template_by_id.get(t_id)
        if not template:
            continue

        # Check if this template actually involves an error
        has_error = any(
            line_by_id[lid].level in ("WARN", "ERROR", "FATAL") 
            for lid in t_line_ids
        )
        
        # Ignore brand new INFO templates. They aren't incidents.
        if not has_error:
            continue

        first_line_id = min(t_line_ids)
        first_line = line_by_id.get(first_line_id)

        # Condition A: Rare template (count < 3)
        is_rare = template.count < 3
        
        # Condition B: First occurrence is in the second half (only for larger files)
        is_late = (first_line_id - 1) >= mid_point and total_lines > 50

        if is_rare or is_late:
            reason = "Rare error template" if is_rare else "Late-appearing error template"
            anomalies.append(
                Anomaly(
                    type="new_template",
                    timestamp=first_line.timestamp if first_line else None,
                    line_ids=t_line_ids,
                    template_ids=[t_id],
                    description=f"{reason} ({template.count} occ): {template.pattern}",
                )
            )

    return anomalies