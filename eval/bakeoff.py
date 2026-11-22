"""LT-23: Local model bake-off.

Runs the eval pipeline against multiple Ollama models, measuring:
- Accuracy (from scorer)
- False positives
- JSON validity (parse failures count)
- Latency per case (seconds)
- Token usage (prompt + completion)

Usage:
    python eval/bakeoff.py

Requires Ollama running locally with the models already pulled.
Override models via LOGTLDR_BAKEOFF_MODELS (comma-separated).
"""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import litellm
import yaml

litellm.drop_params = True

from logtldr.anomaly_detector import detect_anomalies  # noqa: E402
from logtldr.citation_validator import validate_citations  # noqa: E402
from logtldr.compressed_view import build_compressed_view  # noqa: E402
from logtldr.config import Config  # noqa: E402
from logtldr.drain_wrapper import template_logs  # noqa: E402
from logtldr.ingest import ingest  # noqa: E402
from logtldr.llm_gateway import LLMGateway  # noqa: E402
from logtldr.models import FinalSummary, LLMResponse  # noqa: E402
from logtldr.parse import parse  # noqa: E402
from logtldr.redaction import redact  # noqa: E402
from logtldr.refuse_gate import check_refuse_gate  # noqa: E402
from logtldr.rules_engine import apply_rules  # noqa: E402

# Add the eval dir to path so we can import scorer
sys.path.insert(0, str(Path(__file__).parent))
from scorer import ExpectedOutcome, score_run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "eval" / "cases"
RESULTS_DIR = ROOT / "eval" / "results"

VIEW_TOKEN_BUDGET = int(os.environ.get("LOGTLDR_VIEW_BUDGET", "5000"))

# Models to test — override with LOGTLDR_BAKEOFF_MODELS=model1,model2
DEFAULT_MODELS = [
    "ollama/llama3.1:8b",
    "ollama/qwen2.5:7b",
]


def _log(msg: str, end: str = "\n") -> None:
    """Print with flush so output streams in real time."""
    print(msg, end=end, flush=True)


def run_single_case(
    log_path: Path, model: str
) -> tuple[FinalSummary, list, bool, float, int]:
    """Run one case. Returns (summary, lines, used_llm, latency_s, json_errors)."""
    raw_lines = ingest(str(log_path))
    lines = parse(raw_lines)
    redacted = redact(lines)
    templates, assignments = template_logs(redacted)
    anomalies = detect_anomalies(redacted, templates, assignments)

    gate_status, _ = check_refuse_gate(redacted, anomalies)
    if gate_status != "proceed":
        return (
            FinalSummary(status=gate_status, verdict="Refused by gate"),
            redacted,
            False,
            0.0,
            0,
        )

    config = Config(
        model=model,
        api_base="http://localhost:11434",
        local=True,
        api_key=None,
    )
    gateway = LLMGateway(config)

    view = build_compressed_view(
        redacted, templates, anomalies, max_tokens=VIEW_TOKEN_BUDGET
    )

    json_errors = 0
    t0 = time.perf_counter()
    try:
        llm_response = gateway.call_summarize(view, LLMResponse)
        assert isinstance(llm_response, LLMResponse)
    except RuntimeError as e:
        # JSON parse failed after retries
        json_errors = 1
        latency = time.perf_counter() - t0
        dummy = FinalSummary(status="error", verdict=str(e))
        return dummy, redacted, True, latency, json_errors
    latency = time.perf_counter() - t0

    validated = validate_citations(llm_response, redacted)
    final_summary = apply_rules(validated)

    return final_summary, redacted, True, latency, json_errors


def run_model(model: str, cases: list[Path]) -> dict:
    """Run all cases for one model and return results dict."""
    _log(f"\n{'='*60}")
    _log(f"  MODEL: {model}")
    _log(f"{'='*60}")

    cases_to_score = []
    latencies: list[float] = []
    json_errors_total = 0

    for i, case_dir in enumerate(cases):
        log_path = case_dir / "input.log"
        gt_path = case_dir / "ground_truth.yaml"

        if not log_path.exists() or not gt_path.exists():
            continue

        gt_data = yaml.safe_load(gt_path.read_text(encoding="utf-8"))
        expected = ExpectedOutcome(
            case_id=case_dir.name,
            expected_status=gt_data.get("status", "ok"),
            cause_keywords=gt_data.get("cause_keywords", []),
            evidence_line_ids=gt_data.get("evidence_line_ids", []),
        )

        _log(f"\n  [{i+1}/{len(cases)}] {case_dir.name}", end="")
        _log(" ... ", end="")

        try:
            summary, redacted_lines, used_llm, latency, jerr = run_single_case(
                log_path, model
            )
            cases_to_score.append((expected, summary, redacted_lines))
            latencies.append(latency)
            json_errors_total += jerr

            status_icon = "✓" if summary.status != "error" else "✗"
            _log(f"{status_icon}  {latency:.1f}s  status={summary.status}")

            if summary.hypotheses:
                cause = summary.hypotheses[0].cause
                _log(f"         cause: {cause[:80]}{'...' if len(cause) > 80 else ''}")
            elif summary.status == "error" and summary.verdict:
                _log(f"         ERROR: {summary.verdict[:80]}")

        except Exception as e:
            _log(f"✗  CRASH: {e}")
            dummy = FinalSummary(status="error", verdict=str(e))
            cases_to_score.append((expected, dummy, []))
            latencies.append(0.0)
            json_errors_total += 1

    # Score
    report = score_run(cases_to_score)
    result_dict = report.to_dict()

    # Add per-case cause/verdict for debugging
    cases_list: list[dict] = result_dict["cases"]  # type: ignore[assignment]
    for case_result, (_exp, summary, _lines) in zip(cases_list, cases_to_score):
        if summary.hypotheses:
            case_result["actual_cause"] = summary.hypotheses[0].cause
        else:
            case_result["actual_cause"] = None
        case_result["verdict"] = summary.verdict

    # Compute aggregates
    avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
    total_latency = sum(latencies)
    json_valid_pct = (
        (1 - json_errors_total / len(cases_to_score)) * 100
        if cases_to_score
        else 0.0
    )

    model_result = {
        "model": model,
        "accuracy": result_dict["accuracy"],
        "diagnosis": result_dict["diagnosis"],
        "healthy": result_dict["healthy"],
        "unknown": result_dict["unknown"],
        "json_errors": json_errors_total,
        "json_valid_pct": round(json_valid_pct, 1),
        "avg_latency_s": round(avg_latency, 1),
        "total_latency_s": round(total_latency, 1),
        "latencies": [round(lat, 1) for lat in latencies],
        "cases": cases_list,
    }

    # Print model summary
    _log(f"\n  --- {model} SUMMARY ---")
    accuracy = float(result_dict["accuracy"])  # type: ignore[arg-type]
    _log(f"  Accuracy:        {accuracy * 100:.1f}%")
    diag = result_dict["diagnosis"]
    _log(f"  Diagnosis:       {diag['correct']}/{diag['total']}")  # type: ignore[index]
    h = result_dict["healthy"]
    _log(f"  Healthy:         {h['correct']}/{h['total']} (FP: {h['false_positives']})")  # type: ignore[index]
    u = result_dict["unknown"]
    _log(f"  Unknown:         {u['correct']}/{u['total']} (Fabricated: {u['fabricated']})")  # type: ignore[index]
    _log(f"  JSON valid:      {json_valid_pct:.0f}% ({json_errors_total} parse failures)")
    _log(f"  Avg latency:     {avg_latency:.1f}s")
    _log(f"  Total time:      {total_latency:.0f}s")

    return model_result


def print_comparison_table(results: list[dict]) -> None:
    """Print a markdown comparison table."""
    _log("\n")
    _log("=" * 70)
    _log("  BAKE-OFF RESULTS")
    _log("=" * 70)
    _log("")

    # Header
    _log(
        f"| {'Model':<25} | {'Accuracy':>8} | {'Diag':>6} | {'FP':>3} | {'JSON %':>6} | {'Avg Lat':>7} |"
    )
    _log(
        f"|{'-'*27}|{'-'*10}|{'-'*8}|{'-'*5}|{'-'*8}|{'-'*9}|"
    )

    for r in results:
        d = r["diagnosis"]
        fp = r["healthy"]["false_positives"]
        _log(
            f"| {r['model']:<25} "
            f"| {r['accuracy']*100:>7.1f}% "
            f"| {d['correct']}/{d['total']:>3} "
            f"| {fp:>3} "
            f"| {r['json_valid_pct']:>5.0f}% "
            f"| {r['avg_latency_s']:>5.1f}s |"
        )

    _log("")

    # Pick winner
    best = max(results, key=lambda r: (r["accuracy"], r["json_valid_pct"], -r["avg_latency_s"]))
    _log(f"  ★ Baseline pick: {best['model']}")
    _log(f"    (accuracy {best['accuracy']*100:.0f}%, JSON {best['json_valid_pct']:.0f}%, avg {best['avg_latency_s']:.1f}s)")
    _log("")


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True, parents=True)

    models_env = os.environ.get("LOGTLDR_BAKEOFF_MODELS", "")
    models = [m.strip() for m in models_env.split(",") if m.strip()] if models_env else DEFAULT_MODELS

    cases = sorted(
        [d for d in CASES_DIR.iterdir() if d.is_dir() and (d / "ground_truth.yaml").exists()]
    )

    _log(f"logtldr bake-off — {len(models)} models × {len(cases)} cases")
    _log(f"Models: {', '.join(models)}")
    _log(f"Token budget: {VIEW_TOKEN_BUDGET}")

    all_results = []
    for model in models:
        result = run_model(model, cases)
        all_results.append(result)

    # Print comparison
    print_comparison_table(all_results)

    # Save full results
    date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_file = RESULTS_DIR / f"{date_str}_bakeoff.json"
    out_file.write_text(json.dumps(all_results, indent=2), encoding="utf-8")
    _log(f"Full results saved to {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()