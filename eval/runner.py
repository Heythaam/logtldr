"""Eval runner script (LT-20)."""

import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from logtldr.anomaly_detector import detect_anomalies
from logtldr.citation_validator import validate_citations
from logtldr.compressed_view import build_compressed_view
from logtldr.config import VIEW_BUDGET_CLOUD, VIEW_BUDGET_LOCAL, Config
from logtldr.drain_wrapper import template_logs
from logtldr.ingest import ingest
from logtldr.llm_gateway import LLMGateway
from logtldr.models import FinalSummary, LLMResponse
from logtldr.parse import parse
from logtldr.redaction import redact
from logtldr.refuse_gate import check_refuse_gate
from logtldr.rules_engine import apply_rules

# Add the eval dir to path so we can import scorer
sys.path.insert(0, str(Path(__file__).parent))
from scorer import ExpectedOutcome, score_run  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
CASES_DIR = ROOT / "eval" / "cases"
RESULTS_DIR = ROOT / "eval" / "results"

# Token budget for compressed view — auto-detected from model prefix.
# Local models (ollama/) use 3000 to stay responsive on consumer hardware;
# cloud APIs use 5000.  Override with LOGTLDR_VIEW_BUDGET env var.
_BUDGET_OVERRIDE = os.environ.get("LOGTLDR_VIEW_BUDGET")


def _resolve_view_budget(model: str) -> int:
    """Pick token budget: env var wins, then auto-detect from model prefix."""
    if _BUDGET_OVERRIDE is not None:
        return int(_BUDGET_OVERRIDE)
    return VIEW_BUDGET_LOCAL if model.startswith("ollama/") else VIEW_BUDGET_CLOUD

# Seconds to sleep between LLM calls (rate-limit cooldown).
# Groq free tier needs ~65s; Gemini/OpenAI can use 1-5s; local needs 0.
SLEEP_SECONDS = int(os.environ.get("LOGTLDR_EVAL_SLEEP", "65"))


def _resolve_api_key() -> str | None:
    """Read API key from environment, provider-agnostic.

    Priority: LOGTLDR_API_KEY > provider-specific vars.
    Returns None if nothing is set (LiteLLM will try its own env detection).
    """
    return (
        os.environ.get("LOGTLDR_API_KEY")
        or os.environ.get("GROQ_API_KEY")
        or os.environ.get("GEMINI_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
        or os.environ.get("ANTHROPIC_API_KEY")
    )


def run_pipeline(log_path: Path) -> tuple[FinalSummary, list, bool]:
    """Run the in-process pipeline.

    Returns (summary, redacted_lines, used_llm).
    """
    raw_lines = ingest(str(log_path))
    lines = parse(raw_lines)
    redacted = redact(lines)
    templates, assignments = template_logs(redacted)
    anomalies = detect_anomalies(redacted, templates, assignments)

    gate_status, _ = check_refuse_gate(redacted, anomalies)
    if gate_status != "proceed":
        return FinalSummary(status=gate_status, verdict="Refused by gate"), redacted, False

    # Configure LLM via environment variables
    api_key = _resolve_api_key()
    model = os.environ.get("LOGTLDR_MODEL", "groq/openai/gpt-oss-20b")
    is_local = model.startswith("ollama/")
    api_base = os.environ.get("LOGTLDR_API_BASE", "http://localhost:11434") if is_local else None

    config = Config(model=model, api_base=api_base, local=is_local, api_key=api_key)
    gateway = LLMGateway(config)

    budget = _resolve_view_budget(model)
    view = build_compressed_view(redacted, templates, anomalies, max_tokens=budget)
    llm_response = gateway.call_summarize(view, LLMResponse)
    assert isinstance(llm_response, LLMResponse)

    validated = validate_citations(llm_response, redacted)
    final_summary = apply_rules(validated, redacted)

    return final_summary, redacted, True


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True, parents=True)

    cases_to_score = []
    cases = sorted([d for d in CASES_DIR.iterdir() if d.is_dir()])

    model = os.environ.get("LOGTLDR_MODEL", "groq/openai/gpt-oss-20b")
    budget = _resolve_view_budget(model)
    print(f"Model: {model}  |  Sleep: {SLEEP_SECONDS}s  |  Token budget: {budget}")
    print()

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

        print(f"Running: {case_dir.name}...")
        try:
            summary, redacted_lines, used_llm = run_pipeline(log_path)
            cases_to_score.append((expected, summary, redacted_lines))

            # Print cause for debugging
            print(f"  status: {summary.status}")
            if summary.hypotheses:
                print(f"  cause: {summary.hypotheses[0].cause}")
            else:
                print("  cause: (no hypotheses)")

            # Sleep between LLM calls to respect rate limits
            if used_llm and i < len(cases) - 1 and SLEEP_SECONDS > 0:
                print(f"  Sleeping {SLEEP_SECONDS}s (rate limit)...")
                time.sleep(SLEEP_SECONDS)
        except Exception as e:
            print(f"  [ERROR] Pipeline failed: {e}")
            # Create a dummy summary just so the case is recorded as a failure
            dummy = FinalSummary(status="error", verdict=str(e))
            cases_to_score.append((expected, dummy, []))

    # Score the entire run
    report = score_run(cases_to_score)

    # Build enriched results with cause text for debugging
    result_dict = report.to_dict()
    cases_list: list[dict[str, object]] = result_dict["cases"]  # type: ignore[assignment]
    for case_result, (_expected, summary, _lines) in zip(cases_list, cases_to_score):
        if summary.hypotheses:
            case_result["actual_cause"] = summary.hypotheses[0].cause
        else:
            case_result["actual_cause"] = None
        case_result["verdict"] = summary.verdict

    # Save results
    date_str = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    out_file = RESULTS_DIR / f"{date_str}_eval.json"
    out_file.write_text(json.dumps(result_dict, indent=2), encoding="utf-8")

    # Print summary to console
    print("\n--- EVAL SUMMARY ---")
    print(f"Model: {model}")
    print(f"Accuracy: {report.accuracy * 100:.1f}%")
    print(f"Diagnosis: {report.diagnosis_correct}/{report.diagnosis_total}")
    print(f"Healthy: {report.healthy_correct}/{report.healthy_total} (False Positives: {report.false_positives})")
    print(f"Unknown: {report.unknown_correct}/{report.unknown_total} (Fabricated: {report.fabricated})")
    print(f"Results saved to {out_file.relative_to(ROOT)}")


if __name__ == "__main__":
    main()