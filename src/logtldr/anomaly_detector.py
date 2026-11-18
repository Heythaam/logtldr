"""Anomaly detection (AD-5)."""

import re
from collections import defaultdict

from logtldr.models import Anomaly, LogLine, Template

# Regexes to detect stack trace patterns
_TRACEBACK_START_RE = re.compile(r"Traceback \(most recent call last\)")
_FRAME_RE = re.compile(r"^\s+")


def detect_anomalies(
    lines: list[LogLine], templates: list[Template], assignments: dict[int, str]
) -> list[Anomaly]:
    """Detect anomalies from parsed lines and templates."""
    anomalies: list[Anomaly] = []
    if not lines:
        return anomalies

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

    # 2. Detect Grouped Stack Traces
    anomalies.extend(_detect_stack_traces(lines))

    # 3. Detect New / Rare Templates
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

        has_error = any(
            line_by_id[lid].level in ("WARN", "ERROR", "FATAL") for lid in t_line_ids
        )
        
        # Ignore brand new INFO templates.
        if not has_error:
            continue

        first_line_id = min(t_line_ids)
        first_line = line_by_id.get(first_line_id)

        is_rare = template.count < 3
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

    # 4. Detect Frequency Spikes
    anomalies.extend(_detect_frequency_spikes(lines, assignments))

    return anomalies


def _detect_stack_traces(lines: list[LogLine]) -> list[Anomaly]:
    """Detect consecutive lines that look like Python or Java stack traces."""
    anomalies: list[Anomaly] = []
    current_group: list[int] = []
    
    for line in lines:
        msg = line.message
        
        # Start of a new traceback
        if _TRACEBACK_START_RE.search(msg):
            if current_group:
                anomalies.append(_make_stack_anomaly(current_group, lines))
            current_group = [line.line_id]
        # Continuation of a traceback
        elif current_group and _FRAME_RE.match(msg):
            current_group.append(line.line_id)
        # End of a traceback
        else:
            if current_group:
                anomalies.append(_make_stack_anomaly(current_group, lines))
                current_group = []
                
    if current_group:
        anomalies.append(_make_stack_anomaly(current_group, lines))
        
    return anomalies


def _make_stack_anomaly(group: list[int], lines: list[LogLine]) -> Anomaly:
    line_by_id = {line.line_id: line for line in lines}
    first_line = line_by_id[group[0]]
    return Anomaly(
        type="grouped_stack_trace",
        timestamp=first_line.timestamp,
        line_ids=group,
        description=f"Multi-line stack trace detected ({len(group)} lines)",
    )


def _detect_frequency_spikes(
    lines: list[LogLine], assignments: dict[int, str]
) -> list[Anomaly]:
    """Detect templates that appear significantly more often in a bucket."""
    anomalies: list[Anomaly] = []
    if len(lines) < 20:
        return anomalies

    line_by_id = {line.line_id: line for line in lines}
    
    # Determine bucket strategy
    has_timestamps = all(line.timestamp is not None for line in lines)
    buckets: dict[int, str] = {}
    
    if has_timestamps:
        min_time = min(line.timestamp for line in lines if line.timestamp)
        for line in lines:
            if line.timestamp:
                delta = (line.timestamp - min_time).total_seconds()
                buckets[line.line_id] = str(int(delta // 60))
    else:
        bucket_size = max(1, len(lines) // 10)
        for line in lines:
            buckets[line.line_id] = str((line.line_id - 1) // bucket_size)

    template_bucket_counts: dict[str, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    for line in lines:
        t_id = assignments.get(line.line_id)
        if t_id:
            b_id = buckets[line.line_id]
            template_bucket_counts[t_id][b_id].append(line.line_id)

    for t_id, b_counts in template_bucket_counts.items():
        # Only look at templates that happen frequently overall
        total_occurrences = sum(len(ids) for ids in b_counts.values())
        if total_occurrences < 5:
            continue
            
        # Only care if the template actually involves an error or warning
        all_line_ids = [lid for ids in b_counts.values() for lid in ids]
        has_error = any(
            line_by_id[lid].level in ("WARN", "ERROR", "FATAL") 
            for lid in all_line_ids
        )
        if not has_error:
            continue

        num_buckets = len(set(buckets.values()))
        avg_per_bucket = total_occurrences / num_buckets

        for b_id, line_ids in b_counts.items():
            # Spike definition: > 5x the average AND at least 5 occurrences in one bucket
            if len(line_ids) > (avg_per_bucket * 5) and len(line_ids) >= 5:
                first_line = line_by_id[line_ids[0]]
                anomalies.append(
                    Anomaly(
                        type="frequency_spike",
                        timestamp=first_line.timestamp,
                        line_ids=line_ids,
                        template_ids=[t_id],
                        description=f"Spike detected: {len(line_ids)} occurrences in bucket {b_id}",
                    )
                )

    return anomalies