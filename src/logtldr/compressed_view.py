"""Compressed view builder (AD-3)."""

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Any, Optional

from logtldr.models import Anomaly, CompressedView, LogLine, Template

_ENCODING_NAME = "cl100k_base"
# tiktoken caches each encoding under the SHA-1 of its download URL. litellm
# (a hard dependency) ships this exact file, so no download is ever needed.
_BUNDLED_FILE = "9b5ad71b2ce5302211f9c61530b329a4922fc6a4"
# Rough chars-per-token ratio for the last-resort estimate.
_CHARS_PER_TOKEN = 4

# Loaded on first use, not at import: importing this module must never touch
# the network (LT-46). False means "tried and failed, use the estimate".
_encoding: Any = None
_encoding_failed = False
_MAX_TOKENS = 8000
_MAX_TEMPLATES = 30
_CONTEXT_RADIUS = 5
# Share of the budget the anomaly list (+ its timeline entries) may use,
# so anomalies can never crowd out the sample lines entirely.
_ANOMALY_SHARE = 0.4
# Per-item counts ignore token merges across item boundaries and the
# truncation note; the margin absorbs that. The final count is exact.
_SAFETY_MARGIN = 150


def _bundled_cache_dir() -> Optional[str]:
    """litellm's bundled tokenizer folder, found without importing litellm."""
    spec = importlib.util.find_spec("litellm")
    if spec is None or spec.origin is None:
        return None
    folder = Path(spec.origin).parent / "litellm_core_utils" / "tokenizers"
    return str(folder) if (folder / _BUNDLED_FILE).is_file() else None


def _load_encoding() -> Any:
    """Load cl100k_base, preferring sources that need no network.

    1. litellm's bundled copy (offline, exact counts).
    2. tiktoken's normal lookup (user cache, or a download when online).
    Returns None if both fail; callers then estimate.
    """
    import tiktoken

    bundled = _bundled_cache_dir()
    if bundled is not None:
        previous = os.environ.get("TIKTOKEN_CACHE_DIR")
        os.environ["TIKTOKEN_CACHE_DIR"] = bundled
        try:
            return tiktoken.get_encoding(_ENCODING_NAME)
        except Exception:
            pass
        finally:
            if previous is None:
                os.environ.pop("TIKTOKEN_CACHE_DIR", None)
            else:
                os.environ["TIKTOKEN_CACHE_DIR"] = previous

    try:
        return tiktoken.get_encoding(_ENCODING_NAME)
    except Exception:
        return None


def _get_encoding() -> Any:
    """Cached encoding, or None if unavailable (warns once on stderr)."""
    global _encoding, _encoding_failed
    if _encoding is None and not _encoding_failed:
        _encoding = _load_encoding()
        if _encoding is None:
            _encoding_failed = True
            print(
                "logtldr: tokenizer unavailable offline; using approximate token counts",
                file=sys.stderr,
            )
    return _encoding


def _count_text(text: str) -> int:
    """Token count of text: exact if the tokenizer loaded, else ~len/4."""
    encoding = _get_encoding()
    if encoding is None:
        return max(1, len(text) // _CHARS_PER_TOKEN) if text else 0
    return len(encoding.encode(text))


def _tokens(obj: Any) -> int:
    """Token count of obj serialized compactly (close to model_dump_json)."""
    return _count_text(json.dumps(obj, separators=(",", ":"), ensure_ascii=False))


def _count_view(view: CompressedView) -> int:
    """Token count of what is sent to the LLM (exact when the tokenizer loaded)."""
    return _count_text(view.model_dump_json(exclude={"estimated_tokens"}))


def _serialize_line(line: LogLine) -> dict[str, Any]:
    ts = line.timestamp  # local binding lets mypy narrow away None
    return {
        "line_id": line.line_id,
        "redacted_text": line.redacted_text,
        "timestamp": ts.isoformat() if ts is not None else None,
        "level": line.level,
    }


def _timeline_entry(anomaly: Anomaly) -> dict[str, Any] | None:
    ts = anomaly.timestamp
    if ts is None:
        return None
    return {
        "timestamp": ts.isoformat(),
        "anomaly_type": anomaly.type,
        "description": anomaly.description,
    }


def _candidate_line_ids(
    line_dict: dict[int, LogLine],
    templates: list[Template],
    anomalies: list[Anomaly],
) -> list[int]:
    """All wanted line IDs, highest priority first, deduplicated.

    Order: anomaly lines themselves, then their context in widening rings
    (±1, ±2, ... ±5) across all anomalies, then one exemplar per template
    from most to least frequent. Truncating this list from the end therefore
    drops low-frequency exemplars first, then the outermost context lines.
    """
    ordered: list[int] = []
    seen: set[int] = set()

    def push(lid: int) -> None:
        if lid in line_dict and lid not in seen:
            seen.add(lid)
            ordered.append(lid)

    for dist in range(_CONTEXT_RADIUS + 1):
        for anomaly in anomalies:
            for lid in anomaly.line_ids:
                push(lid - dist)
                push(lid + dist)

    for t in sorted(templates, key=lambda t: (-t.count, t.template_id)):
        push(t.first_occurrence_line_id)

    return ordered


def build_compressed_view(
    lines: list[LogLine], templates: list[Template], anomalies: list[Anomaly],
    max_tokens: int = _MAX_TOKENS,
) -> CompressedView:
    """Build a deterministic CompressedView under the 8000 token budget."""
    if not lines:
        return CompressedView(
            templates=[], anomalies=[], timeline_candidates=[], sample_lines=[],
            statistics={}, metadata={}, estimated_tokens=0,
        )

    total_lines = len(lines)
    line_dict = {line.line_id: line for line in lines}
    budget = max_tokens - _SAFETY_MARGIN

    # 1. Fixed part: top templates, statistics, metadata
    templates_data = [
        {"template_id": t.template_id, "pattern": t.pattern, "count": t.count}
        for t in sorted(templates, key=lambda t: (-t.count, t.template_id))[:_MAX_TEMPLATES]
    ]
    statistics = {
        "total_lines": total_lines,
        "error_count": sum(1 for line in lines if line.level in ("ERROR", "FATAL")),
        "warning_count": sum(1 for line in lines if line.level == "WARN"),
    }
    metadata = {"log_format": lines[0].source_format}
    used = _tokens({
        "templates": templates_data, "anomalies": [], "timeline_candidates": [],
        "sample_lines": [], "statistics": statistics, "metadata": metadata,
    })

    # 2. Anomalies, greedily, within their share of the budget
    anomaly_budget = int(budget * _ANOMALY_SHARE)
    kept_anomalies: list[Anomaly] = []
    anomalies_data: list[dict[str, Any]] = []
    timeline: list[dict[str, Any]] = []
    anomaly_used = 0
    for anomaly in anomalies:
        data = anomaly.model_dump(mode="json")  # mode="json" makes datetimes serializable
        entry = _timeline_entry(anomaly)
        cost = _tokens(data) + 1 + (_tokens(entry) + 1 if entry else 0)
        if anomaly_used + cost > anomaly_budget:
            break
        kept_anomalies.append(anomaly)
        anomalies_data.append(data)
        if entry:
            timeline.append(entry)
        anomaly_used += cost
    used += anomaly_used
    timeline.sort(key=lambda x: x["timestamp"])
    dropped_anomalies = len(anomalies) - len(kept_anomalies)

    # 3. Sample lines, greedily, in priority order; each line is tokenized once
    candidates = _candidate_line_ids(line_dict, templates, kept_anomalies)
    samples: list[dict[str, Any]] = []
    for lid in candidates:
        entry = _serialize_line(line_dict[lid])
        cost = _tokens(entry) + 1  # +1 for the separating comma
        if used + cost > budget:
            break
        samples.append(entry)
        used += cost

    # 4. Build, count exactly, and trim in chunks only if the margin was too small
    while True:
        num_sampled = len(samples)
        notes = []
        if num_sampled < total_lines:
            notes.append(f"{num_sampled} lines sampled from {total_lines}")
        if dropped_anomalies:
            notes.append(f"{dropped_anomalies} of {len(anomalies)} anomalies omitted")
        view = CompressedView(
            templates=templates_data,
            anomalies=anomalies_data,
            timeline_candidates=timeline,
            sample_lines=sorted(samples, key=lambda e: e["line_id"]),
            truncation_note="; ".join(notes) or None,
            statistics=statistics,
            metadata=metadata,
            estimated_tokens=0,
        )
        view.estimated_tokens = _count_view(view)
        if view.estimated_tokens <= max_tokens or not samples:
            return view
        del samples[-max(1, len(samples) // 20):]  # drop lowest-priority 5%