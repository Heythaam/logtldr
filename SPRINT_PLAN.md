---
title: logtldr Sprint Plan (4 weeks, solo)
created: 2026-11-12
---

# logtldr Sprint Plan

## Definition of Done (all stories)
- Tests pass in CI (pytest deterministic suite)
- Code passes `ruff check` and `mypy`
- User-facing changes documented (README, help text, or comments)
- No redaction leaks or unverified citations
- Acceptance criteria met and verified

---

### LT-01: CLI skeleton with argparse
- Epic: Foundation
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: none
- Refs: FR-P0-13, AD-1
- Description: Create src/logtldr/main.py with argparse for --local, --json, --model, --yes flags. Wire CLI to accept stdin or file input. Exit cleanly with code 1 on errors.
- Acceptance criteria:
  - [ ] `logtldr --help` shows all flags with descriptions
  - [ ] `echo "test" | logtldr` runs without crashing (may fail downstream)
  - [ ] `logtldr --unknown-flag` exits with error code 1

### LT-02: pyproject.toml, setuptools src/ layout, package data
- Epic: Foundation
- Priority: P0
- Week: 1
- Estimate: S
- Depends on: none
- Refs: FR-P0-13, AD-8
- Description: Set up src/logtldr/ layout with pyproject.toml. Declare dependencies (drain3, litellm, pydantic, pytest, ruff, mypy). Declare direct_causes.yaml as package data.
- Acceptance criteria:
  - [ ] `pip install -e .` installs logtldr
  - [ ] `from logtldr import main` imports successfully
  - [ ] `importlib.resources.files('logtldr').joinpath('direct_causes.yaml')` finds the catalog

### LT-03: GitHub Actions CI workflow (deterministic tests, lint, type-check)
- Epic: Foundation
- Priority: P0
- Week: 1
- Estimate: S
- Depends on: LT-02
- Refs: FR-P0-13, NFR-*, AD-8
- Description: Set up .github/workflows/ci.yml to run pytest (deterministic only, no LLM), ruff, mypy on every push. Store results in CI logs.
- Acceptance criteria:
  - [ ] Workflow runs on a test commit
  - [ ] `pytest tests/` passes locally before push
  - [ ] Workflow fails if ruff or mypy errors introduced

### LT-04: Log ingest (stdin, file, pipe detection)
- Epic: Parsing
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: LT-01
- Refs: FR-P0-1, AD-1, AD-2
- Description: Implement src/logtldr/ingest.py. Read logs from stdin (default), file (positional arg), or pipes. Detect format hints (JSON lines, plaintext, pytest, docker, npm). Return RawLogLine[] (format, raw_text).
- Acceptance criteria:
  - [ ] `cat app.log | logtldr` reads from stdin
  - [ ] `logtldr app.log` reads from file
  - [ ] Format detection identifies JSON vs plaintext correctly
  - [ ] `tests/test_parse.py::test_ingest_*` pass

### LT-05: Parse JSON lines and plaintext with timestamps
- Epic: Parsing
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: LT-04, LT-13
- Refs: FR-P0-1, AD-1, AD-2
- Description: Implement src/logtldr/parse.py parsers for JSON lines (extract all fields) and plaintext (ISO8601/syslog timestamps, log level, message). Extract line_id, timestamp, level, message into LogLine.
- Acceptance criteria:
  - [ ] `tests/test_parse.py::test_json_lines` passes
  - [ ] `tests/test_parse.py::test_plaintext_with_timestamps` passes
  - [ ] Handles missing fields gracefully

### LT-06: Parse pytest, Docker build, npm test output
- Epic: Parsing
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: LT-05
- Refs: FR-P0-1, AD-1
- Description: Add format-specific parsers for pytest (FAILED/ERROR/PASSED), Docker build logs (Step X/Y, RUN output), npm test output (FAIL/PASS). Reuse timestamp and level extraction.
- Acceptance criteria:
  - [ ] `tests/test_parse.py::test_pytest_format` passes
  - [ ] `tests/test_parse.py::test_docker_format` passes
  - [ ] `tests/test_parse.py::test_npm_format` passes

### LT-07: Fallback line-by-line parser
- Epic: Parsing
- Priority: P0
- Week: 1
- Estimate: S
- Depends on: LT-05
- Refs: FR-P0-1, AD-1
- Description: Implement fallback: parse unknown formats as line-by-line. Set line_id, original_text, message = full line. No timestamp or level extraction.
- Acceptance criteria:
  - [ ] `tests/test_parse.py::test_fallback` passes
  - [ ] Unknown format produces valid LogLine[]

### LT-08: Redaction patterns (AWS, JWT, DB URL, Slack, env vars)
- Epic: Redaction
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: LT-04, LT-13
- Refs: FR-P0-2, NFR-2, AD-6, AD-11
- Description: Implement src/logtldr/redaction.py with Redactor class. Patterns: AWS keys (AKIA...), JWTs (ey...), DB URLs (postgresql://user:pass@...), Slack tokens (xoxb-...), env var assignments (VAR=...). Apply most-specific patterns first. Use stable placeholders (host_1, email_1, token_1).
- Acceptance criteria:
  - [ ] Placeholder map is stable: same secret → same placeholder in run
  - [ ] Placeholder map lives in Redactor only, not on LogLine
  - [ ] `tests/test_redaction.py::test_stable_mapping` passes

### LT-09: Redaction leak tests (real token formats: AWS, JWT, DB, Slack)
- Epic: Redaction
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: LT-08
- Refs: FR-P0-2, NFR-2, AD-6
- Description: Unit tests for each redaction pattern with real secret formats. 3–5 test cases per pattern. Run in CI on every push. Verify no secrets appear in redacted_text.
- Acceptance criteria:
  - [ ] `tests/test_redaction_leaks.py` runs and all cases pass (15+ patterns)
  - [ ] Each pattern (AWS, JWT, DB URL, Slack, env var) has ≥3 test cases
  - [ ] CI runs these tests; any failure blocks merge

### LT-10: Drain template mining (message field only, masking for numbers/hex/UUID)
- Epic: Template Mining
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: LT-05, LT-13
- Refs: FR-P0-3, AD-1, AD-5b
- Description: Implement src/logtldr/drain_wrapper.py. Use drain3 on message field only (not timestamp/level). Enable masking: (\d+) → <NUM>, 0x[a-f0-9]+ → <HEX>, UUID → <UUID>. Default depth=4, st=0.5 (calibrate week 1).
- Acceptance criteria:
  - [ ] `tests/test_drain.py::test_template_mining` passes
  - [ ] Template IDs stable across re-runs
  - [ ] Numbers/hex/UUIDs masked in templates

### LT-11: Anomaly detection: new templates and first error
- Epic: Anomaly Detection
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: LT-10, LT-13
- Refs: FR-P0-4, AD-1, AD-5, AD-8
- Description: Implement src/logtldr/anomaly_detector.py. Detect "new_template" (first occurrence after first 50% of window, or count < 3). Detect "first_error" (first WARN/ERROR/FATAL line). Return Anomaly[] with type, timestamp, line_ids.
- Acceptance criteria:
  - [ ] `tests/test_anomaly.py::test_new_template` passes
  - [ ] `tests/test_anomaly.py::test_first_error` passes
  - [ ] Anomalies include line_ids and timestamps

### LT-12: Anomaly detection: frequency spikes and grouped stacks
- Epic: Anomaly Detection
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: LT-11
- Refs: FR-P0-4, AD-1, AD-5, AD-8
- Description: Detect "frequency_spike": template count increases significantly in a time bucket (if timestamps exist) or line-position bucket (fallback). Detect "grouped_stack_trace": consecutive lines matching Python/Java frame pattern.
- Acceptance criteria:
  - [ ] `tests/test_anomaly.py::test_frequency_spike` passes
  - [ ] `tests/test_anomaly.py::test_grouped_stack_trace` passes

### LT-13: Pydantic models (all BaseModel)
- Epic: Core
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: none
- Refs: AD-2
- Description: Implement src/logtldr/models.py: LogLine, Template, Anomaly, CompressedView, Citation, Hypothesis, LLMResponse, FinalSummary. All extend BaseModel. Include docstrings for complex fields.
- Acceptance criteria:
  - [ ] All models importable from logtldr.models
  - [ ] `mypy src/logtldr/models.py` passes (no errors)
  - [ ] `pytest --doctest-modules src/logtldr/models.py` passes

### LT-14: Refuse-to-guess gate (nothing_unusual, not_enough_signal)
- Epic: Anomaly Detection
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: LT-12
- Refs: FR-P0-5, AD-1
- Description: Implement src/logtldr/refuse_gate.py. Return status="nothing_unusual" if Anomaly[] empty (no LLM call). Return status="not_enough_signal" if total_lines < 50 OR parseable% < 30%. Return statistics (total_lines, parseable_lines, error_count, warning_count).
- Acceptance criteria:
  - [ ] `tests/test_refuse_gate.py::test_nothing_unusual_no_llm` passes
  - [ ] `tests/test_refuse_gate.py::test_not_enough_signal` passes
  - [ ] Deterministic stats reported for both cases

### LT-15: Eval case setup (10 cases: LogHub subset + CI failures)
- Epic: Evaluation
- Priority: P0
- Week: 1
- Estimate: M
- Depends on: none
- Refs: FR-P0-14, AD-7
- Description: Set up eval/cases/ folder structure. Gather 10 eval cases: 3–5 LogHub public datasets (for anomaly/false-positive checks) + 5–7 real CI failures from your repos (pytest, Docker, npm). Create ground_truth.yaml for each with cause, cause_keywords, status, expected_unknowns (optional).
- Acceptance criteria:
  - [ ] eval/cases/01_*/input.log through eval/cases/10_*/input.log exist
  - [ ] Each has ground_truth.yaml with cause, status
  - [ ] At least 1 healthy-logs case (status: nothing_unusual)
  - [ ] At least 1 misleading case (correlation not causation)

---

### LT-16: LLM gateway setup (LiteLLM, config model/api_base, --local localhost check)
- Epic: LLM Integration
- Priority: P0
- Week: 2
- Estimate: M
- Depends on: LT-13
- Refs: FR-P0-6, AD-4
- Description: Implement src/logtldr/llm_gateway.py. LLMGateway class reads config (model, api_base, api_key). If --local, validate api_base hostname against allowlist (localhost, 127.0.0.1, ::1). If no API key and Ollama not reachable, raise clear error.
- Acceptance criteria:
  - [ ] `LLMGateway(config)` with --local and non-localhost api_base raises ValueError
  - [ ] `LLMGateway(config)` with local=True and api_base="http://localhost:11434" succeeds
  - [ ] Error message is clear: "Check your config (api_base=...)"

### LT-17: Structured output with LLMResponse schema and retry logic
- Epic: LLM Integration
- Priority: P0
- Week: 2
- Estimate: M
- Depends on: LT-16, LT-13
- Refs: FR-P0-6, AD-2, AD-4
- Description: LLMGateway.call_summarize returns LLMResponse (Pydantic). Try json_schema format first (OpenAI/Claude); fallback to JSON mode + Pydantic validation. Retry up to 3 times; on validation error, feed error back to LLM with "Please return valid JSON."
- Acceptance criteria:
  - [ ] `tests/test_llm_gateway.py::test_json_schema_success` passes
  - [ ] `tests/test_llm_gateway.py::test_retry_on_invalid_json` passes (mock LLM returns invalid, then valid)
  - [ ] After 3 failures, raises RuntimeError with clear message

### LT-18: Citation validator (line exists, snippet matches in redacted version)
- Epic: LLM Integration
- Priority: P0
- Week: 2
- Estimate: M
- Depends on: LT-08, LT-13, LT-17
- Refs: FR-P0-7, AD-1, AD-6
- Description: Implement src/logtldr/citation_validator.py. For each Citation in LLMResponse: check line_id exists, snippet appears exactly in redacted_text. Mark citations with citation_valid bool. If all citations invalid for top hypothesis, promote next hypothesis; if none remain, return status="not_enough_signal".
- Acceptance criteria:
  - [ ] `tests/test_citation_validator.py::test_valid_citation` passes (snippet found)
  - [ ] `tests/test_citation_validator.py::test_invalid_citation` passes (snippet not found)
  - [ ] `tests/test_citation_validator.py::test_drop_invalid_promote_next` passes

### LT-19: Langfuse integration (log pipeline steps for debugging)
- Epic: Observability
- Priority: P0
- Week: 2
- Estimate: M
- Depends on: LT-16
- Refs: FR-P0-6, AD-4
- Description: Add Langfuse tracing to pipeline steps: ingest, parse, redact, drain, anomaly, refuse_gate, llm_call, citation_validate, rules_engine, render. Log timestamps, counts (lines, templates, anomalies). Langfuse off in --local mode or only to local instance.
- Acceptance criteria:
  - [ ] `tests/test_llm_gateway.py::test_langfuse_disabled_in_local_mode` passes
  - [ ] Traces can be viewed in Langfuse dashboard (manual verification)
  - [ ] No impact on latency when tracing disabled

### LT-20: Eval runner script (calls logtldr --json, parses output, scores)
- Epic: Evaluation
- Priority: P0
- Week: 2
- Estimate: M
- Depends on: LT-15, LT-17
- Refs: FR-P0-14, AD-7
- Description: Implement eval/runner.py. For each eval case: run `logtldr --json eval/cases/NN/input.log`, parse JSON output, call scorer.score(output, ground_truth). Aggregate results (correct_count, false_positives, citations_valid_count).
- Acceptance criteria:
  - [ ] `python eval/runner.py` runs and outputs results to results/{date}_{model}.json
  - [ ] Results include correct_count, false_positive_count, citations_valid_count per case
  - [ ] JSON is well-formed and human-readable

### LT-21: Scorer logic (cause_keywords, citations, false positives)
- Epic: Evaluation
- Priority: P0
- Week: 2
- Estimate: M
- Depends on: LT-15, LT-17
- Refs: FR-P0-14, AD-7, AD-10
- Description: Implement eval/scorer.py. Score each case: "correct" = cause_keyword match + all citations valid. Separate scoring for unknowns (not part of correct/wrong). Count false positives when healthy logs return status="ok".
- Acceptance criteria:
  - [ ] `tests/test_scorer.py::test_correct_with_valid_citations` passes
  - [ ] `tests/test_scorer.py::test_wrong_cause` fails score
  - [ ] `tests/test_scorer.py::test_false_positive_on_healthy` counted

### LT-22: K8s incident simulation (10 cases: killed DB, full disk, bad config, OOM, etc.)
- Epic: Evaluation
- Priority: P0
- Week: 2
- Estimate: L
- Depends on: LT-15
- Refs: FR-P0-14, AD-7
- Description: Simulate Kubernetes failures on your local cluster or minikube. Trigger: kill DB pod (produces connection timeouts), fill disk (disk full warnings), bad config (invalid key error), OOM (oomkilled event), network issue, etc. Capture logs, annotate with expected root cause. Add 10 cases to eval/cases/11_* through eval/cases/20_*.
- Acceptance criteria:
  - [ ] eval/cases/11_* through eval/cases/20_* exist with input.log and ground_truth.yaml
  - [ ] Each case has a known, documented root cause
  - [ ] Logs include both anomalies and context before/after

### LT-23: Local model bake-off (test 2–3 models, token counter, document results)
- Epic: Evaluation
- Priority: P0
- Week: 2
- Estimate: L
- Depends on: LT-20, LT-21, LT-22
- Refs: FR-P0-6, NFR-1, AD-4
- Description: Test 2–3 local 7–8B instruct models on Ollama (e.g., Qwen 8B, Llama 8B). Run full eval (10+10 cases) on each. Measure: accuracy %, false positives, latency, token usage. Document results in a bake-off table. Choose one as baseline for README.
- Acceptance criteria:
  - [ ] Bake-off results table: model name, accuracy %, false positives, avg latency (16GB CPU and GPU)
  - [ ] Token counter integrated (use tiktoken estimate or Ollama's token reporting)
  - [ ] Document per-hardware latency (CPU vs GPU)

### LT-24: Compressed view sampling (deterministic: ±N + exemplars, token counter, budget enforcement)
- Epic: LLM Integration
- Priority: P0
- Week: 2
- Estimate: M
- Depends on: LT-13, LT-23
- Refs: FR-P0-3, AD-3
- Description: Implement compressed_view_builder() in src/logtldr/. Select lines deterministically: ±5 around each anomaly + 1 exemplar per template. Use real token counter (tiktoken for OpenAI, estimate for Ollama). Enforce 4–8k token budget; remove exemplars with lowest frequency if over budget.
- Acceptance criteria:
  - [ ] `tests/test_compressed_view.py::test_deterministic_sampling` passes (same input → same output)
  - [ ] Compressed view estimated_tokens within 5% of actual count
  - [ ] Budget enforced: no CompressedView exceeds 8000 tokens

---

### LT-25: Evidence typing (LLM proposes, code downgrades if not in catalog)
- Epic: Rules Engine
- Priority: P0
- Week: 3
- Estimate: M
- Depends on: LT-17, LT-27
- Refs: FR-P0-8, AD-5, AD-5b
- Description: Implement src/logtldr/rules_engine.py. LLM outputs evidence_type per hypothesis. Code validates: "direct" accepted only if cited line is WARN/ERROR/FATAL AND matches direct-cause catalog. Otherwise downgrade to "temporal", cap confidence at "medium".
- Acceptance criteria:
  - [ ] `tests/test_rules_engine.py::test_direct_evidence_in_catalog` passes
  - [ ] `tests/test_rules_engine.py::test_downgrade_temporal_to_medium` passes
  - [ ] Unmatched "direct" claims become "temporal"

### LT-26: Confidence capping (temporal always → medium; code enforced)
- Epic: Rules Engine
- Priority: P0
- Week: 3
- Estimate: S
- Depends on: LT-25
- Refs: FR-P0-8, AD-5
- Description: In rules_engine, enforce confidence cap: temporal evidence capped at "medium" by code, not by LLM. Direct evidence can be high/medium/low per LLM. No override possible.
- Acceptance criteria:
  - [ ] `tests/test_rules_engine.py::test_temporal_cap_medium` passes
  - [ ] Direct evidence confidence unchanged
  - [ ] Confidence in FinalSummary reflects cap

### LT-27: Direct-cause catalog v1 (~20 patterns, YAML format)
- Epic: Rules Engine
- Priority: P0
- Week: 3
- Estimate: M
- Depends on: none
- Refs: FR-P0-8, AD-5, AD-5b
- Description: Create src/logtldr/direct_causes.yaml with ~20 patterns: OOMKilled, disk full, permission denied, invalid config key, port in use, cert expired, migration failed, connection refused (context-dependent), timeout (context-dependent), etc. Each: pattern name, keywords, level (ERROR/FATAL), description.
- Acceptance criteria:
  - [ ] `src/logtldr/direct_causes.yaml` has ≥20 patterns
  - [ ] YAML parses cleanly (no syntax errors)
  - [ ] Each pattern has keywords, level, description fields

### LT-28: Rule-based unknowns (time window, single service, external dep, truncation)
- Epic: Rules Engine
- Priority: P0
- Week: 3
- Estimate: M
- Depends on: LT-13, LT-25
- Refs: FR-P0-9, AD-5
- Description: In rules_engine, generate unknowns[] based on rules: short time window (< 5 min for service logs, not CI), single service present + external dep, logs end abruptly, evidence points to external dependency. Never left to LLM.
- Acceptance criteria:
  - [ ] `tests/test_rules_engine.py::test_unknowns_time_window` passes
  - [ ] `tests/test_rules_engine.py::test_unknowns_external_dep` passes
  - [ ] FinalSummary.unknowns includes all applicable warnings

### LT-29: Verdict line formatting ("Evidence points to X, confidence: Y")
- Epic: Output
- Priority: P0
- Week: 3
- Estimate: S
- Depends on: LT-13, LT-26
- Refs: FR-P0-10, AD-1
- Description: In FinalSummary, format verdict line: "Evidence points to <cause>, confidence: <level>" (if status="ok"). Omit "consider: <action>" if "check next" advice is cut/hidden.
- Acceptance criteria:
  - [ ] `tests/test_renderer.py::test_verdict_line_format` passes
  - [ ] Verdict line never says "root cause is"
  - [ ] Confidence level included

### LT-30: Timeline formatting (anomaly-first, A → B → crash links, line numbers)
- Epic: Output
- Priority: P0
- Week: 3
- Estimate: M
- Depends on: LT-13, LT-28
- Refs: FR-P0-10, AD-1
- Description: Build timeline in FinalSummary. Start at first anomaly (not first error). Link events: "Line 5: warning → Line 12: spike → Line 45: crash". Include timestamps and line numbers. Each event has citations.
- Acceptance criteria:
  - [ ] `tests/test_renderer.py::test_timeline_anomaly_first` passes
  - [ ] Timeline events are in chronological order
  - [ ] Line numbers match compressed view

### LT-31: Terminal renderer (fits one screen: verdict, timeline, unknowns, check_next)
- Epic: Output
- Priority: P0
- Week: 3
- Estimate: M
- Depends on: LT-29, LT-30
- Refs: FR-P0-10, AD-1, AD-1
- Description: Implement src/logtldr/renderer.py TerminalRenderer. Output: status, verdict line, timeline (top hypothesis only), unknowns, check_next. Format fits one terminal screen (~30 lines max, 80 chars wide).
- Acceptance criteria:
  - [ ] Default output (no --json) is human-readable and fits one screen
  - [ ] `tests/test_renderer.py::test_terminal_output_fits_screen` passes
  - [ ] Reads well with no truncation

### LT-32: JSON renderer (full structured output, all fields)
- Epic: Output
- Priority: P0
- Week: 3
- Estimate: S
- Depends on: LT-31
- Refs: FR-P0-10, AD-1
- Description: Implement JSONRenderer. Output: full FinalSummary as JSON (all hypotheses, timeline, unknowns, check_next). Use for tooling and eval.
- Acceptance criteria:
  - [ ] `logtldr --json` produces valid JSON
  - [ ] `tests/test_renderer.py::test_json_output` passes
  - [ ] `python -m json.tool` on output succeeds

### LT-33: Deploy/startup markers (regex: version, startup, config reload)
- Epic: Features (P1)
- Priority: P1 (cut order 2)
- Week: 3
- Estimate: M
- Depends on: LT-13
- Refs: FR-P1-2, AD-5
- Description: In rules_engine, detect deploy/startup markers: regex for "starting v2.3.1", "config reloaded at", "initialized at". Output in verdict if present: "Errors started 40s after v2.3.1 started."
- Acceptance criteria:
  - [ ] `tests/test_rules_engine.py::test_deploy_marker_version` passes
  - [ ] `tests/test_rules_engine.py::test_deploy_marker_startup` passes
  - [ ] Marker timing included in output

### LT-34: Specific "check next" advice (LLM generates, eval guardrail: hide if generic)
- Epic: Features (P1)
- Priority: P1 (cut order 3)
- Week: 3
- Estimate: M
- Depends on: LT-20, LT-21
- Refs: FR-P1-3, AD-5
- Description: LLM outputs check_next field (specific guidance). Eval guardrail: run full eval by end of week 3. If advice is still generic ("check network", "restart service") in >2 cases, hide section by default; ship without it rather than hurt trust.
- Acceptance criteria:
  - [ ] LLMResponse.check_next is populated by LLM
  - [ ] `tests/test_rules_engine.py::test_generic_advice_flagged` detects generic patterns
  - [ ] Decision logged: ship or hide for v1.1

### LT-35: Upstream dependency heuristics (connection refused, timeout, 5xx → check <dep>)
- Epic: Features (P1)
- Priority: P1 (cut order 1)
- Week: 3
- Estimate: M
- Depends on: LT-25, LT-27
- Refs: FR-P1-1, AD-5
- Description: Detect "connection refused", "timeout", "5xx error" without other direct evidence. Flag: "Cause is likely in upstream <dependency>. Check its logs." Rank hypotheses by confidence.
- Acceptance criteria:
  - [ ] `tests/test_rules_engine.py::test_upstream_connection_refused` passes
  - [ ] FinalSummary shows alternative hypothesis for upstream
  - [ ] Confidence is medium or lower (not direct evidence)

### LT-36: Full eval set (25–30 cases: add healthy + misleading + K8s)
- Epic: Evaluation
- Priority: P0
- Week: 3
- Estimate: M
- Depends on: LT-22
- Refs: FR-P0-14, AD-7
- Description: Complete eval set to 25–30 cases. Add: 3–5 healthy-logs (false-positive checks), 3–5 misleading (correlation looks like causation, external dep hidden). Total: 10 LogHub + 5 CI + 10 K8s + healthy + misleading. Update each with ground_truth.yaml.
- Acceptance criteria:
  - [ ] eval/cases/ has 25–30 cases total
  - [ ] At least 3 healthy cases with status: "nothing_unusual"
  - [ ] At least 3 misleading cases with expected_unknowns

### LT-37: Evals pass (run full eval set end of week 3; all cases scored)
- Epic: Evaluation
- Priority: P0
- Week: 3
- Estimate: M
- Depends on: LT-20, LT-21, LT-36
- Refs: FR-P0-14, AD-7
- Description: Run `python eval/runner.py` on all 25–30 cases. Score against ground truth. Report: correct_count / total, false_positives, citations_valid_count, per-model accuracy. Use results to decide P1 cut order (especially LT-34).
- Acceptance criteria:
  - [ ] `python eval/runner.py` completes without error
  - [ ] Results JSON: correct_count, false_positive_count, citations_valid per case
  - [ ] Baseline model (API or local) achieves ≥60% accuracy on in-log cases

---

### LT-38: --local enforcement (gateway rejects non-localhost, error message is clear)
- Epic: Security
- Priority: P0
- Week: 4
- Estimate: M
- Depends on: LT-16, LT-39
- Refs: FR-P0-12, AD-4
- Description: Hardening: --local mode fails closed. LLMGateway rejects non-localhost endpoints immediately. Error message: "Check your config (api_base=...) or omit --local for external API." Langfuse off or local-only.
- Acceptance criteria:
  - [ ] `tests/test_llm_gateway_local.py::test_local_rejects_non_localhost` passes
  - [ ] Non-localhost error raised, never silently falls back
  - [ ] Error message is helpful

### LT-39: Network-blocked CI test (pytest-socket: --local succeeds, external blocked)
- Epic: Security
- Priority: P0
- Week: 4
- Estimate: M
- Depends on: LT-16, LT-18
- Refs: FR-P0-12, NFR-*, AD-4
- Description: Use pytest-socket to block network access. Two tests: (a) --local + non-localhost → error immediately, (b) --local with network blocked succeeds via mocked local server. Runs in CI.
- Acceptance criteria:
  - [ ] `tests/test_llm_gateway_local.py::test_local_rejects_non_localhost_immediately` passes (no retry)
  - [ ] `tests/test_llm_gateway_local.py::test_local_with_blocked_network_succeeds` passes (mocked local Ollama)
  - [ ] CI runs both tests

### LT-40: README (usage, examples, model selection, no secrets policy, --local explanation)
- Epic: Documentation
- Priority: P0
- Week: 4
- Estimate: M
- Depends on: LT-37, LT-31, LT-32
- Refs: NFR-4, NFR-2
- Description: Write README.md: CLI usage examples (CLI args, stdin, pipes), model selection guide (API vs local bake-off results), --local mode explanation, no-secrets guarantee, install via pip, output format (terminal vs --json).
- Acceptance criteria:
  - [ ] README has "Quick Start" section with 3 examples
  - [ ] Model selection includes bake-off results table (accuracy %, latency per model/hardware)
  - [ ] --local mode explained clearly
  - [ ] "No secrets are sent" section with proof (link to tests)

### LT-41: Eval results table (accuracy %, false positives, latency, cost per model)
- Epic: Documentation
- Priority: P0
- Week: 4
- Estimate: M
- Depends on: LT-37
- Refs: FR-P0-14, NFR-1
- Description: Create table in README: results from eval/results/{date}_{model}.json. Columns: model name, accuracy (%), false positives (%), latency (sec/100k lines), cost (tokens/summary or $). Include API model (OpenAI/Anthropic) and local models (Ollama).
- Acceptance criteria:
  - [ ] Table in README with ≥2 models (1 API, 1 local)
  - [ ] Columns: accuracy, false positives, latency, cost
  - [ ] Data sourced from eval/results/*.json

### LT-42: Demo GIF (failed CI run → logtldr summary)
- Epic: Documentation
- Priority: P0
- Week: 4
- Estimate: M
- Depends on: LT-31, LT-40
- Refs: FR-P0-14
- Description: Create GIF or video of logtldr usage: 1) show failed CI run log, 2) run `logtldr --json` on it, 3) show summary output (verdict, timeline, advice). Embed in README.
- Acceptance criteria:
  - [ ] GIF is < 5 sec, shows full pipeline
  - [ ] Visible in README.md
  - [ ] Demo case has clear cause (not trivial)

### LT-43: PyPI publish (version bump, setup release process, tag)
- Epic: Launch
- Priority: P0
- Week: 4
- Estimate: M
- Depends on: LT-02, LT-40, LT-39
- Refs: FR-P0-13
- Description: Prepare for PyPI: version bump in pyproject.toml, ensure README and all metadata clean, run final CI, tag release (v0.1.0-alpha or similar), publish via `python -m build && twine upload`.
- Acceptance criteria:
  - [ ] pyproject.toml version set
  - [ ] Build succeeds: `python -m build`
  - [ ] Package installable: `pip install logtldr==<version>`
  - [ ] `logtldr --version` outputs version

---

## Per-Week Summary

| Week | Stories | Story IDs | Est. (S) | Est. (M) | Est. (L) | Total Hours |
|------|---------|-----------|----------|----------|----------|------------|
| **1** | 15 | LT-01 to LT-15 | 3 | 11 | 1 | ~50–60h |
| **2** | 9 | LT-16 to LT-24 | 0 | 4 | 2 | ~40–50h |
| **3** | 13 | LT-25 to LT-37 | 2 | 10 | 1 | ~50–60h |
| **4** | 7 | LT-38 to LT-43 | 0 | 6 | 1 | ~40–50h |

**Total: 44 stories, 5 S-tasks, 31 M-tasks, 5 L-tasks (~180–220 hours for solo dev over 4 weeks)**

### Overload Assessment

- **Week 1 (50–60h):** Heavy; foundation + parsing + redaction + template mining + anomaly detection. Pushing but manageable if parsing is straightforward.
- **Week 2 (40–50h):** Moderate. LLM + citation validator are focused work. LT-22 (K8s sim) and LT-23 (model bake-off) are L-tasks but parallelizable.
- **Week 3 (50–60h):** Heavy. Rules engine + output rendering + all P1 features + evals completion. This is the crunch week if behind.
- **Week 4 (40–50h):** Moderate. Security hardening, docs, release. Less novel code; mostly integration.

### Buffer Rule Applied

If behind at end of week 3, cut P1 in order:
1. **LT-35: Upstream heuristics** (first to cut)
2. **LT-33: Deploy markers** (second to cut)
3. **LT-34: "Check next" advice** (third to cut)
4. **LT-19: Langfuse** (fourth to cut, but marked P0 for tracking; reduce to basic logging only)

### Notes

- **Parallelization:** LT-22 and LT-23 can run in parallel during week 2. Eval cases can be added incrementally (10 → 20 → 30).
- **Week 1 risk:** Parsing formats is a rabbit hole. Stick to the strict list (JSON, plaintext, pytest, Docker, npm) + fallback.
- **Week 2–3 handoff:** Evals must be runnable by end of week 2 so week 3 can use results to decide on LT-34.
- **Definition of Done:** Enforced at CI (every push). No story marked done until tests pass + ruff/mypy clean.
