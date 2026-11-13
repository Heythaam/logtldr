---
title: logtldr MVP
status: final
created: 2026-11-12
updated: 2026-11-12
---

# logtldr: Incident Summary from Noisy Logs

## Problem & Users

**Target users:** On-call developers, DevOps engineers, and SREs in small-to-mid teams without a heavy observability stack (Datadog, ELK, New Relic). Also developers debugging failed CI runs and long test output locally.

**Pain points:**
- The first error in logs is rarely the root cause; the cause is buried in warnings or odd messages minutes earlier, hidden in noise.
- 95% of lines are repetitive (health checks, request logs); the signal is impossible to find under pressure.
- Current workflows: `kubectl logs`, `grep -i "error"`, manual scrolling to reconstruct what happened, or copy-pasting logs into ChatGPT—which leaks secrets, hits context limits, and loses the order of events.
- Bigger teams use dashboards (Datadog, Grafana) but still end up running `kubectl logs` because dashboards show **THAT** something broke (spike in errors), not **WHY** it broke.

**Decision in minutes:** On-call engineers need to decide in ~10 minutes: roll back? escalate? dig deeper? ignore? The tool must earn trust fast or they'll stick with existing workflows.

## Positioning

**What logtldr is:** A fast first look at noisy logs in the terminal. Turns thousands of lines into a short incident summary: what happened, in what order, where it went wrong, the likely root cause, and what to check next. Every claim is backed by exact log lines.

**What it's not:** A Datadog or observability-platform replacement. It works on logs you already have access to (terminal, files, pipes) and gives you a 30-second triage before you decide whether to escalate to a full investigation.

**Trust by design:**
- Every claim cites the exact log line it comes from (verified by code, not asserted by the LLM).
- Confidence levels are explicit and enforced by code, not left to the model. Temporal correlations are capped at medium confidence; only direct evidence (a log line stating the cause) can be high.
- The code refuses to guess: healthy logs → "nothing unusual detected"; too little data → "not enough signal". No invented causes.
- Unknowns are always explicit: "logs cover only 2 minutes, the cause may be outside these logs".

**Local-first:** Works fully offline with Ollama. Logs never leave your machine unless you explicitly send them. No account, no API key required to get started.

## Use Cases

1. **Production incident:** `kubectl logs my-pod | logtldr` or `docker logs my-container | logtldr` (or `logtldr app.log`)
2. **Failed CI run:** `gh run view <run-id> --log-failed | logtldr` (or copy logs into a file)
3. **Local development:** Long test output or a stack trace wall; same tool, same speed.

## Functional Requirements

### P0 (Non-negotiable for launch)

**FR-P0-1: Pipeline & Parsing**
- Ingest logs from stdin, files, or pipes.
- Parse: JSON lines, plaintext with timestamps, Python/Java stack traces, pytest output, Docker build logs, npm test output.
- Fallback: plain line-by-line parsing for unknown formats.
- Constraint: strict format list only; no attempt to parse all formats (rabbit hole).

**FR-P0-2: Redaction & Secret Leakage Prevention**
- Redact tokens, API keys, passwords, IPs, email addresses, database URLs before any processing.
- Patterns: AWS credentials, JWTs, database connection strings, Slack tokens, any string matching `token=...` or similar.
- Stable placeholders: use consistent identifiers (host_1, email_1, token_1, etc.) so the same entity stays recognizable across lines.
- Guarantee: nothing from the logs is sent to an LLM in unredacted form.
- Test: deterministic test cases with real token formats (AWS keys, JWTs) must pass.

**FR-P0-3: Drain Template Mining**
- Use drain3 to identify repeating log templates and group similar lines.
- Output: a compressed view of the logs (templates + counts), not a summary yet.

**FR-P0-4: Anomaly Detection**
- Identify anomalies in the compressed log stream: new templates (not seen before), frequency spikes, first error, grouped stack traces.
- Determine the earliest anomaly (the first unusual thing), not just the first error.
- Output: list of anomalies with timestamps and line numbers.

**FR-P0-5: Refuse-to-Guess Path**
- If no anomalies are detected: output "nothing unusual detected in these logs" and do not invoke the LLM.
- If too little data: output "not enough signal for a reliable summary" and report deterministic stats (template count, error count). Threshold: fewer than 50 lines, OR less than 30% of lines parseable (matched to a template or error pattern). [Proposed thresholds; refine in week 1 based on LogHub eval cases.]
- Goal: prevent false positives and over-confident answers.

**FR-P0-6: LLM Call & Structured Output**
- Use LiteLLM to abstract over API models (OpenAI, Anthropic) and local models (Ollama via localhost).
- Output schema (Pydantic):
  - `status`: "ok" | "nothing_unusual" | "not_enough_signal"
  - `verdict`: one-line summary (if status is "ok")
  - `hypotheses[]`: list of `{cause, evidence_type, confidence, citations[]}` (if status is "ok"; MVP displays top hypothesis only)
  - `timeline[]`: list of `{time, event, citations[]}` (if status is "ok")
  - `unknowns[]`: list of limitations and caveats
  - `check_next`: specific guidance (if "check next" advice is included)
  - `citation`: structured as `{line_id, snippet}` where snippet is a short excerpt from the redacted line the LLM saw
- Retry logic: if local model returns malformed JSON, retry up to 3 times.
- Test 2–3 local 7–8B instruct models (Ollama, e.g., Qwen 8B, Llama 8B) in week 2 to validate JSON quality, accuracy, and latency; document results in README including per-hardware latency (16 GB RAM laptop, with and without GPU).

**FR-P0-7: Citation Validator**
- Every claim in the summary must cite exact log lines.
- Citation format: line ID + verbatim snippet (a short excerpt from that line).
- Validator: for each cited line, check (a) the line ID exists and (b) the verbatim snippet appears exactly in that line in the redacted version (what the LLM saw).
- Rule: no semantic matching; checks are deterministic and bullet-proof.
- User display: original line numbers (before redaction) are shown to the user; snippets are validated against the redacted version.
- Reject unverified citations: if a citation fails validation, drop the claim or mark it unverified.

**FR-P0-8: Confidence Levels & Evidence Typing**
- LLM proposes an `evidence_type` per cause: "direct" or "temporal".
- Code validation:
  - "Direct" evidence: accepted only if at least one cited line is a WARN/ERROR/FATAL level log and matches a curated direct-cause pattern (OOMKilled, out of memory, no space left on device, permission denied, invalid/unknown config key, missing env var, port already in use, certificate expired, migration failed, etc.).
  - Otherwise: downgraded to "temporal", and confidence is capped at medium by code.
- Catalog: start with ~20 direct-cause patterns; grow from eval failures. Catalog is plain YAML for easy extension and open-source contribution.

**FR-P0-9: Rule-Based Unknowns**
- Triggered by code, not left to the LLM. Output an "unknowns" section when:
  - Time window is short (< 5 minutes) — applies to service logs only, not CI/test output (CI runs are short by nature)
  - Only one service's logs are present
  - Logs end abruptly (incomplete capture)
  - Evidence points to an external dependency (another service, database, external API)
- Examples: "logs cover only 2 minutes, the cause may be outside this window"; "errors point to a database timeout, check the database logs separately".

**FR-P0-10: Verdict Line & Timeline**
- Verdict line: one-line summary in the format "Evidence points to X, confidence: Y" (or "Evidence points to X, confidence: Y, consider: [action suggestion]" if "check next" advice is included; if "check next" is hidden or cut, the "consider:" part is omitted).
- Never say "root cause is X"; always say "evidence points to X".
- Timeline: start at the first anomaly (not the first error), and explicitly link the chain of events: A → B → crash, with line numbers and timestamps.
- Output format (default): fits on one screen. Includes verdict, timeline, evidence, and unknowns.
- JSON output (--json): full structured output with all fields, for tooling and evals.

**FR-P0-12: --local Flag (Code-Enforced Privacy)**
- Flag: `--local` guarantees no external LLM calls.
- Implementation:
  - LLM gateway (LiteLLM) is configured to accept only localhost endpoints; non-localhost URLs → error, never a silent fallback.
  - Langfuse tracing: off, or only to a locally-hosted Langfuse instance. No telemetry to external services.
  - Redaction: always runs in local mode (output gets shared in Slack, tickets, post-mortems).
- CI test: network access is blocked; `logtldr --local` must pass.
- Default: if no API key is configured, local mode is the default. Sending logs to an external API is explicit opt-in with a one-time notice explaining what gets sent (redacted, compressed view only; never raw logs).

**FR-P0-13: Packaging & CLI**
- CLI entry point: `logtldr [--local] [--json] [--model <name>] [file]` (or stdin).
- No config file required for MVP; optional `~/.logtldr/config.yaml` for model override (week 4 if time).
- Packaging: pip-installable (`pip install logtldr`). Published to PyPI.
- CI: GitHub Actions for lint, pytest, type-check on every push.

**FR-P0-14: Eval Set & Metrics**
- Eval set: 25–30 cases covering:
  - LogHub public log datasets (used for anomaly detection validation and false-positive checks; root-cause ground truth comes from developer's own cases).
  - 5–10 real-world CI failures from developer's own repos (pytest, Docker, npm) with known root causes.
  - 5–10 simulated Kubernetes incidents (killed DB, full disk, bad config, OOM) with known root causes.
  - 3–5 healthy logs with no incident (false-positive tests).
  - 3–5 misleading cases (correlation that looks like causation, external dependencies).
- Deterministic tests (redaction, citation validator, template mining) run in CI on every push.
- Full eval with LLM calls: runs on demand and before each release; scored against ground truth.
- Eval runner: script that calls `logtldr --json`, parses output, and scores against ground truth.

### P1 (Cut in order if behind: priority 1 → 2 → 3 → 4)

**FR-P1-1: Upstream Dependency Heuristics (Priority 1 to cut)**
- Pattern matching: when evidence is "connection refused", "timeout", or "5xx error" without other direct evidence → flag "the cause is likely in an upstream service".
- Example: "connection refused to DB → check database logs; connection refused to payment service → check payment service logs".
- Output: alternatives listed with confidence levels.

**FR-P1-2: Deploy/Startup Markers (Priority 2 to cut)**
- Regex detection: identify version changes ("starting v2.3.1"), startup events, config reloads in logs.
- Output: "errors started 40s after v2.3.1 started" or "errors coincide with config reload at 14:02".
- Value: directly answers the rollback decision ("should I roll back the deploy?").

**FR-P1-3: Specific "Check Next" Advice (Priority 3 to cut)**
- LLM generates specific advice (not generic) based on the cause.
- Examples (good): "check the database pool size" or "verify the config key `database.timeout` was updated".
- Examples (bad): "check your network" or "restart the service".
- Eval guardrail: if evals by end of week 3 show the advice is still generic, hide this section by default. Ship MVP without it rather than ship something that hurts trust.

**FR-P1-4: Langfuse Tracing (Priority 4 to cut)**
- Integration: log each step of the pipeline to Langfuse (for debugging and observability of the summarization process).
- Not user-facing; for the developer's own use.
- Can be skipped if MVP is crunch; cost and latency can be reported without Langfuse.

### Non-Functional Requirements

**NFR-1: Performance**
- API model: under 30 seconds on 100k-line input.
- Local 7–8B model: latency measured and reported per hardware configuration (16 GB RAM laptop, with and without a consumer GPU); no latency guarantee for local models, since CPU-only execution may exceed 30s on large inputs. Baseline local model chosen empirically in week 2 by testing 2–3 candidates (e.g., Qwen 8B Instruct, Llama 8B Instruct) on the eval set.
- Measured on the eval set in week 2; trade-offs and per-hardware latency documented in README.

**NFR-2: Secret Leakage**
- Zero secrets in anything sent to an external LLM.
- Proven by: unit tests covering real token formats (AWS, JWT, DB URL) + CI test of the entire pipeline.

**NFR-3: Citation Accuracy**
- Zero unverified citations shown to the user.
- Every citation is checked deterministically (line ID exists, snippet matches exactly).

**NFR-4: Zero Setup**
- Works out of the box: `pip install logtldr && kubectl logs my-pod | logtldr`.
- No config file required.
- No account required (local-first).
- Explicit opt-in for external LLM access.

## Success Metrics (MVP)

Measured on the eval set (25–30 cases). Baseline targets established in week 2; recalibrated as data comes in.

**In-log cases only** (cases where the root cause IS in the logs; excludes healthy-log and cause-outside-logs cases):

1. **Root cause correctly identified:**
   - API model (OpenAI, Anthropic): ≥ 80% of in-log cases
   - Local 7–8B model (Ollama, chosen in week 2): ≥ 60% of in-log cases
   - Definition of "correct": top hypothesis matches the expected root cause (per written ground truth), AND all citations for that hypothesis pass the validator. A right answer with invalid citations counts as wrong.
   - Status: initial targets; recalibrated after week-2 baseline run.

2. **False positives on healthy logs:** target 0% — no invented causes when logs show normal operation.

3. **Correctly flags "cause outside logs":** target 100% — the summary explicitly states "the cause may be outside these logs" when the root cause is not present in the logs (node drain, DB failover, external service issue).

4. **Compression ratio:** reported (e.g., "1000-line input → 50-line summary = 20x compression").

5. **Cost and latency per summary:** reported (token count, wall-clock latency, any API charges if external model used).

6. **Citations validated:** 100% of citations must pass deterministic validation; unverified citations are dropped or marked.

## Out of Scope (v1.1+)

- GitHub Action for PR comments on failed CI runs (v1.1)
- --markdown output for post-mortems (v1.1)
- --verbose flag (v1.1; --json covers detail for MVP)
- Kubernetes multi-pod mode (merging timelines across pods) (v2)
- Slack bot (v2)
- Web UI (v2)

## Risks & Mitigations

**Risk 1: Hallucinated root causes (confident but wrong)**
- Mitigation: citation validator + code-enforced confidence capping + refuse-to-guess path.
- Mitigation: evidence-type downgrade (temporal → medium confidence only).
- Mitigation: rule-based unknowns surface limitations explicitly.
- Mitigation: evals from day 1 to catch degradation early.

**Risk 2: Secrets leak to external API**
- Mitigation: redaction before any LLM processing (tested with real token formats).
- Mitigation: --local flag with code enforcement (fails closed if non-localhost).
- Mitigation: default to local if no API key configured; external is opt-in with one-time notice.

**Risk 3: Local models (Ollama) produce bad structured output or low accuracy**
- Mitigation: test 2–3 models (Qwen 8B, Llama 8B, etc.) in week 2; validate JSON quality, accuracy, and latency.
- Mitigation: retry logic with validation; fail gracefully with "not enough signal" if quality is too low.
- Mitigation: success target for local models is 60% on in-log cases (vs. 80% for API models); document results honestly in README with per-hardware latency measurements. MVP ships with both API and local options available.

**Risk 4: Parsing becomes a rabbit hole (every log format is different)**
- Mitigation: strict format list (JSON lines, timestamps, stacks, pytest, Docker, npm); other formats fall back to line-by-line parsing.

**Risk 5: K8s incident simulation takes longer than expected**
- Mitigation: start with CI failures and LogHub (free); simulate K8s incidents in weeks 2–3 in parallel.

**Risk 6: Evals take longer than expected**
- Mitigation: eval runner script is automated; cases are added incrementally (10 → 20 → 30). No need to wait for all 30 to launch (launch with 20–25 if needed).

## Timeline

- **Week 1:** Foundation (CLI, packaging, CI), parsing, redaction, Drain, eval v1 (~10 cases).
- **Week 2:** Anomaly detection, LLM + Pydantic, citation validator, Langfuse, eval runner, K8s incidents (~10 more cases).
- **Week 3:** Evidence typing, direct-cause catalog, rule-based unknowns, upstream heuristics, deploy markers, "check next" advice, full eval set (25–30 cases).
- **Week 4:** --local enforcement, CI test, README + eval table + demo GIF, PyPI publish.

**Buffer rule:** If behind at end of week 3, cut in this order: upstream heuristics → deploy markers → "check next" advice → Langfuse. Never cut evals or the citation validator.
