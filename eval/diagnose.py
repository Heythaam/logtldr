"""Diagnostic script for the deterministic pipeline stages (LT-46)."""

from datetime import datetime
from pathlib import Path

import yaml

from logtldr.anomaly_detector import detect_anomalies
from logtldr.drain_wrapper import template_logs
from logtldr.ingest import ingest
from logtldr.parse import parse
from logtldr.redaction import redact
from logtldr.refuse_gate import check_refuse_gate

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "eval" / "cases"
RESULTS_DIR = ROOT / "eval" / "results"


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True, parents=True)
    output_lines: list[str] = []

    # Header
    header = (
        f"{'Case':<30} | {'Format':<12} | {'TS':<5} | {'Levels':<20} | "
        f"{'Tmpl':<5} | {'Anom':<20} | {'Gate'}"
    )
    separator = "-" * len(header)
    output_lines.append(separator)
    output_lines.append(header)
    output_lines.append(separator)

    for case_dir in sorted(CASES_DIR.iterdir()):
        if not case_dir.is_dir():
            continue

        gt_path = case_dir / "ground_truth.yaml"
        log_path = case_dir / "input.log"
        if not gt_path.exists() or not log_path.exists():
            continue

        # Load expected format
        gt = yaml.safe_load(gt_path.read_text(encoding="utf-8"))
        expected_fmt = str(gt.get("expected_format", "unknown"))

        # Run pipeline stages
        raw_lines = ingest(str(log_path))
        detected_fmt = raw_lines[0].format if raw_lines else "none"
        
        # Flag mismatches
        fmt_flag = "" if detected_fmt == expected_fmt else f"(!={expected_fmt})"
        fmt_str = f"{detected_fmt}{fmt_flag}"

        lines = parse(raw_lines)
        ts_count = sum(1 for line in lines if line.timestamp is not None)

        levels: dict[str, int] = {}
        for line in lines:
            levels[line.level] = levels.get(line.level, 0) + 1
        level_str = ",".join(f"{k}:{v}" for k, v in sorted(levels.items()))

        redacted = redact(lines)
        templates, assignments = template_logs(redacted)
        anomalies = detect_anomalies(redacted, templates, assignments)
        gate, _ = check_refuse_gate(redacted, anomalies)

        # Format anomalies
        if anomalies:
            anom_str = f"{len(anomalies)} ({anomalies[0].type})"
        else:
            anom_str = "0"

        # Build row
        row = (
            f"{case_dir.name:<30} | {fmt_str:<12} | {ts_count:<5} | {level_str:<20} | "
            f"{len(templates):<5} | {anom_str:<20} | {gate}"
        )
        output_lines.append(row)

    output_lines.append(separator)
    output_text = "\n".join(output_lines)

    # Print to console
    print(output_text)

    # Save to file
    date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_file = RESULTS_DIR / f"diagnose_{date_str}.txt"
    out_file.write_text(output_text, encoding="utf-8")
    print(f"\nSaved to {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()