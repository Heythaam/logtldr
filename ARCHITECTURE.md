---
title: logtldr Architecture Spine
status: final
created: 2026-11-12
updated: 2026-11-12
---

# logtldr Architecture Spine

## Core Paradigm

**Trust-by-code, not-by-model.** Every claim is verified by deterministic code before shown to the user. Code enforces confidence levels, redaction, citation validity, and unknowns; the LLM proposes, code verifies and downgrades. This keeps hallucinations from reaching users.

---

## AD-1: Pipeline Architecture

**Binds:** Module order and data flow: `Ingest → Parse → Redact → Drain → Anomaly Detect → Refuse Gate → LLM Gateway → Citation Validate → Rules (Evidence/Confidence/Unknowns) → Render`.

**Prevents:** Modules making independent decisions about data format, redaction scope, or confidence levels. Each module has a clear input/output contract (Pydantic models).

**Rule:** A module MUST NOT skip redaction, read the original logs after Drain, or invoke the LLM directly (gateway is the only LLM call point).

**Modules:**

| Module | Responsibility | Input | Output | Boundary |
|--------|-----------------|-------|--------|----------|
| **Ingest** | Read logs from stdin, file, or pipe; detect format hints. | Raw bytes | `RawLogLine[]` (format, raw text) | No parsing; no modification. |
| **Parse** | Break lines into fields (timestamp, level, message); match to parsers (JSON, plaintext, pytest, etc.). | `RawLogLine[]` | `LogLine[]` (line_id, original_text, timestamp, level, message, source_format) | No redaction; no filtering. |
| **Redact** | Replace secrets with stable placeholders (host_1, email_1, token_1, etc.). | `LogLine[]` | `LogLine[]` with `redacted_text` field; placeholder map in memory only. | Stable mapping only; never expose placeholders to the LLM. |
| **Drain** | Template mining with drain3 (message field only; masking for numbers, hex, UUIDs). | `LogLine[]` (redacted) | `Template[]` + template assignments | Only parse message field; no filtering. |
| **Anomaly Detect** | Identify anomalies: new templates (first after X% of window or count < N), frequency spikes (time/line-position buckets), first error. | `LogLine[]` + `Template[]` + assignments | `Anomaly[]` (type, timestamp, involved_templates, sample_lines with line_ids) | No LLM call; no refuse-to-guess logic. |
| **Refuse Gate** | If no anomalies OR too little data, output "nothing_unusual" or "not_enough_signal". | `Anomaly[]` | `status` ∈ {proceed, nothing_unusual, not_enough_signal} + deterministic stats | No LLM call. |
| **LLM Gateway** | Send compressed view to LLM (via LiteLLM); enforce --local; handle retries and JSON validation. | `CompressedView` (see AD-3) | `LLMResponse` (raw JSON before validation) | Only localhost if --local; error if non-localhost and --local set. |
| **Citation Validate** | For each cited line, verify it exists and snippet matches exactly in redacted version. Promote next hypothesis if top loses all citations. | `LogLine[]` (redacted) + `LLMResponse` | `LLMResponse` (may drop invalid hypotheses) | Reject citations; if top hypothesis has no valid citations, drop it and promote next; if none remain, return not_enough_signal. |
| **Rules Engine** | Apply evidence-type downgrade, confidence capping, rule-based unknowns, upstream heuristics (if P1). | `LLMResponse` (validated citations) | `FinalSummary` | Code can only downgrade confidence, never upgrade. |
| **Render** | Format output for terminal (default) or --json. | `FinalSummary` | Text or JSON to stdout | No additional validation; no modification. |

---

## AD-2: Data Models (Pydantic)

All models defined in `src/logtldr/models.py`. All extend `BaseModel`. Cross-module contracts are immutable after creation.

```python
from pydantic import BaseModel
from typing import Optional, List, Dict
from datetime import datetime

class LogLine(BaseModel):
    line_id: int  # original line number before redaction
    original_text: str  # for user display only
    redacted_text: str  # what the LLM sees (secrets replaced with placeholders)
    timestamp: Optional[datetime] = None
    level: str  # "DEBUG", "INFO", "WARN", "ERROR", "FATAL"
    message: str  # parsed message field (what drain3 receives)
    source_format: str  # "plaintext", "json", "pytest", "docker", "npm", etc.

class Template(BaseModel):
    template_id: str  # stable ID from drain3
    pattern: str  # template pattern with wildcards (*, <*>, etc.)
    count: int  # frequency in the log stream
    first_occurrence_line_id: int
    last_occurrence_line_id: int

class Anomaly(BaseModel):
    type: str  # "new_template" | "frequency_spike" | "first_error" | "grouped_stack_trace"
    timestamp: Optional[datetime] = None
    line_ids: List[int]  # lines involved in this anomaly
    template_ids: Optional[List[str]] = None  # if template-based
    description: str  # human-readable

class CompressedView(BaseModel):
    """What the LLM receives. Deterministically sampled and tokenized."""
    templates: List[Dict]  # [{template_id, pattern, count}, ...]
    anomalies: List[Anomaly]  # [anomaly, ...]
    timeline_candidates: List[Dict]  # [{timestamp, anomaly_type, description}, ...]
    sample_lines: List[Dict]  # [{line_id, redacted_text, timestamp, level}, ...] deterministically selected
    truncation_note: Optional[str] = None  # "500 lines sampled from 100k" or similar
    statistics: Dict  # {total_lines, parseable_lines, error_count, warning_count, ...}
    metadata: Dict  # {time_window_start, time_window_end, log_format, ...}
    estimated_tokens: int  # actual token count (not estimate)

class Citation(BaseModel):
    line_id: int
    snippet: str  # short verbatim excerpt from redacted line (what LLM cited)

class Hypothesis(BaseModel):
    cause: str
    evidence_type: str  # "direct" (code-enforced) or "temporal"
    confidence: str  # "high", "medium", "low" (code-enforced cap for temporal)
    citations: List[Citation]

class LLMResponse(BaseModel):
    """Raw output from LLM (before validation + rules)."""
    status: str  # "ok", "nothing_unusual", "not_enough_signal"
    verdict: Optional[str] = None
    hypotheses: Optional[List[Hypothesis]] = None  # LLM proposes; may not all be valid
    timeline: Optional[List[Dict]] = None
    check_next: Optional[str] = None

class FinalSummary(BaseModel):
    """After citation validation + rules engine (final output to user)."""
    status: str  # "ok", "nothing_unusual", "not_enough_signal"
    verdict: Optional[str] = None
    hypotheses: Optional[List[Hypothesis]] = None  # top hypothesis only shown in terminal; all in --json
    timeline: Optional[List[Dict]] = None  # [{time, event, citations}, ...]
    unknowns: List[str] = []  # rule-based unknowns
    check_next: Optional[str] = None
```

---

## AD-3: Compressed View Design & Token Budget

**Binds:** The exact structure, deterministic sampling, and measured token budget sent to the LLM.

**Rule:** The compressed view is deterministic (same input → same output); sampling is reproducible (same order of anomalies, line selection). Token count is measured using a real token counter (e.g., `tiktoken` for OpenAI models, conservative estimate for Ollama).

**Contents:**

- **Templates** (1–2k tokens): Top 20–30 templates by frequency (by count), with pattern and count.
- **Anomalies** (1–2k tokens): All detected anomalies with timestamp, type, and involved line IDs.
- **Timeline** (1–2k tokens): Chronologically ordered {timestamp, anomaly_type, description}.
- **Sample lines** (2–4k tokens): Deterministic selection: ±N lines around each anomaly + one exemplar per template. Selected to preserve context around signal without random sampling.
- **Statistics** (0.5k tokens): Total lines, parseable %, error/warning/info counts, time window.
- **Metadata** (0.5k tokens): Log format, source, sampling info (e.g., "100k lines → 320 sampled").

**Token Budget (measured):** 4–8k tokens in the compressed view (actual measurement with token counter). Leaves room for prompt (1–2k) + output (2–3k) on a 16k context limit (Ollama local models, API models with reasonable limits).

**Sampling algorithm (deterministic):**
```python
def sample_lines(log_lines: List[LogLine], anomalies: List[Anomaly], token_budget: int = 6000) -> List[LogLine]:
    """Deterministically select lines around anomalies + exemplars per template."""
    selected = set()
    
    # 1. For each anomaly, include ±N lines (N calibrated in week 1)
    for anomaly in anomalies:
        for line_id in anomaly.line_ids:
            for delta in range(-5, 6):  # ±5 lines around each anomaly line
                if 0 <= line_id + delta < len(log_lines):
                    selected.add(line_id + delta)
    
    # 2. One exemplar per template (first occurrence of each)
    templates_seen = set()
    for line in log_lines:
        if line.template_id not in templates_seen:
            selected.add(line.line_id)
            templates_seen.add(line.template_id)
    
    # 3. Measure tokens; if over budget, remove least relevant non-anomaly lines
    sampled = [log_lines[i] for i in sorted(selected)]
    tokens = count_tokens(sampled)
    
    if tokens > token_budget:
        # Remove exemplars with lowest frequency first
        # Keep all anomaly lines; those are the signal
        ...
    
    return sorted(sampled, key=lambda l: l.line_id)
```

---

## AD-4: LLM Gateway (LiteLLM)

**Binds:** All LLM calls go through LiteLLM; --local enforcement via URL host parsing; structured output with fallback validation; retry with validation feedback.

**Prevents:** Direct API calls; unvalidated JSON responses; silent fallbacks on error; external calls when --local set.

**Configuration** (`src/logtldr/config.py`):
```python
class Config(BaseModel):
    model: str  # e.g., "ollama/mistral" or "gpt-3.5-turbo" (set in week 2 after testing)
    api_base: str  # e.g., "http://localhost:11434" (Ollama) or "https://api.openai.com/v1" (OpenAI)
    api_key: Optional[str] = None  # from env var LOGTLDR_API_KEY
    local: bool = True  # default: True if no api_key, False if api_key set
    num_ctx: int = 8192  # context length for Ollama (set per model)
    temperature: float = 0.0
    # One-time user confirmation for external API (stored in ~/.logtldr/confirmed_api)
```

**Implementation** (`src/logtldr/llm_gateway.py`):
```python
from urllib.parse import urlparse
import litellm

class LLMGateway:
    def __init__(self, config: Config):
        self.config = config
        self.model = config.model
        self.api_base = config.api_base
        self.local = config.local
        
        # If --local, validate api_base is localhost
        if self.local:
            self._validate_localhost_endpoint(self.api_base)
        
        litellm.api_base = self.api_base
        if config.api_key:
            litellm.api_key = config.api_key
    
    def _validate_localhost_endpoint(self, api_base: str) -> None:
        """Check that api_base is localhost. Fails closed."""
        parsed = urlparse(api_base)
        hostname = parsed.hostname or ""
        allowlist = {"localhost", "127.0.0.1", "::1"}
        
        if hostname not in allowlist:
            raise ValueError(
                f"--local mode requires localhost endpoint; got {hostname}. "
                f"Check your config (api_base={api_base}) or omit --local for external API."
            )
    
    def call_summarize(self, compressed_view: CompressedView, schema: Type[BaseModel]) -> BaseModel:
        """Call LLM and return validated structured output."""
        prompt = self._build_prompt(compressed_view)
        
        for attempt in range(3):
            try:
                # Try json_schema first (OpenAI/Claude support)
                try:
                    response = litellm.completion(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=self.config.temperature,
                        response_format={"type": "json_schema", "schema": schema.model_json_schema()}
                    )
                    raw_text = response.choices[0].message.content
                except Exception:
                    # Fallback: JSON mode + Pydantic validation
                    response = litellm.completion(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=self.config.temperature,
                        **self._get_ollama_params()
                    )
                    raw_text = response.choices[0].message.content
                
                # Validate and parse
                result = schema.model_validate_json(raw_text)
                return result
            
            except (json.JSONDecodeError, ValidationError) as e:
                if attempt < 2:
                    # Feed validation error back in retry prompt
                    prompt += f"\n\n[Validation error: {str(e)[:200]}. Please return valid JSON.]"
                    continue
                else:
                    raise RuntimeError(
                        f"LLM returned invalid JSON after 3 attempts: {e}. "
                        f"Check model capacity (try a larger model or API model)."
                    )
    
    def _get_ollama_params(self) -> Dict:
        """Ollama-specific parameters."""
        if "ollama" in self.model:
            return {"num_ctx": self.config.num_ctx}
        return {}
    
    def _build_prompt(self, compressed_view: CompressedView) -> str:
        # ... (prompt construction)
        pass
```

**--local enforcement:**
- URL host is parsed and checked against allowlist (localhost, 127.0.0.1, ::1). Non-localhost → error, no retry.
- If no API key and Ollama not reachable: clear error "Ollama not running on localhost:11434".
- Langfuse: off in local mode, or only to a local instance.
- CI test: --local mode with network blocked must succeed against a mocked local server.

**One-time API confirmation** (not stdin):
- Never read from stdin (carries the logs). Use `--yes` flag or `LOGTLDR_CONFIRM_API=1` env var.
- If neither set and not a TTY (e.g., piped logs), fail with clear error: "Cannot confirm external API access in non-interactive mode. Use --yes or LOGTLDR_CONFIRM_API=1."
- Store confirmation in `~/.logtldr/confirmed_api` to avoid re-prompting.

---

## AD-5: Rules Engine

**Binds:** Evidence typing, confidence capping, rule-based unknowns, and the direct-cause catalog live here. Code enforces rules; LLM proposes, code verifies.

**Implementation:** `src/rules_engine.py`

**Evidence Typing:**
- LLM outputs `evidence_type` per hypothesis: "direct" or "temporal".
- Code check: "direct" is accepted ONLY if (a) at least one cited line is WARN/ERROR/FATAL and (b) the line matches an entry in the direct-cause catalog.
- Otherwise: downgraded to "temporal", confidence capped at "medium".

**Direct-Cause Catalog** (`src/logtldr/direct_causes.yaml`, package data):
```yaml
direct_causes:
  - pattern: "oom"
    keywords: ["oomkilled", "out of memory", "killed 9"]
    level: [ERROR, FATAL]
    description: "Out of memory condition"
  
  - pattern: "disk_full"
    keywords: ["no space left on device", "disk full"]
    level: [ERROR, FATAL]
    description: "Disk full"
  
  - pattern: "invalid_config"
    keywords: ["unknown config key", "invalid key", "missing required key"]
    level: [ERROR, FATAL]
    description: "Configuration error"
  
  - pattern: "permission_denied"
    keywords: ["permission denied", "access denied", "permission error"]
    level: [ERROR, FATAL]
    description: "Permission or access error"

  # ~16 more patterns (port in use, cert expired, migration failed, etc.)
  # A match here only permits "direct" evidence; it never raises confidence above the LLM's proposal.
```

**Catalog packaging** (`pyproject.toml`):
```toml
[project]
name = "logtldr"
...

[tool.setuptools.package-data]
logtldr = ["direct_causes.yaml"]
```

**Confidence Capping (code-enforced):**
```python
def cap_confidence(evidence_type: str, model_confidence: str) -> str:
    if evidence_type == "temporal":
        return "medium"  # always cap temporal to medium
    elif evidence_type == "direct":
        return model_confidence  # direct can be high, medium, or low
    else:
        return "low"  # default
```

**Rule-Based Unknowns:**
```python
unknowns = []
if log_window_minutes < 5 and log_format != "ci_test":
    unknowns.append("logs cover only {} minutes; cause may be outside this window".format(log_window_minutes))
if single_service and evidence_points_to_external_dep:
    # Only warn about single service if evidence points outside (e.g., connection refused to DB)
    unknowns.append("only one service's logs present; evidence points to a dependency—check its logs separately")
if logs_end_abruptly:
    unknowns.append("logs end abruptly (possible truncation)")
if evidence_points_to_external_dep and not single_service:
    # Also warn if external dep is evident but multiple services are present
    unknowns.append("errors point to an external dependency; check its logs separately")
```

**Anomaly Type Definitions** (`src/logtldr/anomaly_detector.py`):
```python
# "new_template": first occurrence after first X% of the time window (calibrate X in week 1)
#                 OR template with count < N (rare threshold, calibrate N).
# "frequency_spike": template count increases significantly in a time bucket (or line-position bucket if no timestamps).
#                    Detected by comparing buckets to baseline.
# "first_error": first WARN/ERROR/FATAL level message in the log.
# "grouped_stack_trace": consecutive lines matching a stack trace pattern (Python/Java frames).
```

**Upstream Dependency Heuristics (P1, cut if behind):**
- Pattern: "connection refused", "timeout", "5xx" without other direct evidence.
- Action: Flag "cause is likely upstream in <service>"; list alternatives with confidence levels.

---

## AD-5b: Drain Template Mining Configuration

**Implementation** (`src/logtldr/drain_wrapper.py`):
```python
from drain3 import LogMasker, DrainLogClusterer

drain = DrainLogClusterer(
    log_format=None,  # don't parse; we pre-parse to message field
    masking_instructions=[
        (r"(\d+\.){3}\d+", "<IP>"),  # IP addresses
        (r"([0-9a-f]{8}-[0-9a-f]{4})+", "<UUID>"),  # UUIDs
        (r"0x[0-9a-f]+", "<HEX>"),  # hex numbers
        (r"\d+", "<NUM>"),  # numeric fields
    ],
    depth=4,  # tree depth (default)
    st=0.5,  # similarity threshold (calibrate in week 1)
)

def template_logs(lines: List[LogLine]) -> Tuple[List[Template], Dict[int, str]]:
    """Mine templates from message fields only."""
    assignments = {}  # line_id -> template_id
    for line in lines:
        template_id = drain.add_log_message(line.message)
        assignments[line.line_id] = template_id
    
    # Extract templates
    templates = []
    for cluster_id, cluster in drain.clusters.items():
        templates.append(Template(
            template_id=str(cluster_id),
            pattern=cluster.template,
            count=cluster.size,
            first_occurrence_line_id=...,  # track from cluster
            last_occurrence_line_id=...
        ))
    return templates, assignments
```

**Calibration (week 1):**
- `st` (similarity threshold): empirically tune to avoid over-clustering and under-clustering.
- `masking_instructions`: may add more patterns based on observed log formats.

---

## AD-6: Redaction

**Binds:** Stable placeholder mapping, kept in memory, preserves original line numbers. LLM sees only placeholders, never originals.

**Implementation:** `src/logtldr/redaction.py`

**Placeholder strategy:**
- Each unique secret (hostname, email, IP, token, etc.) gets a stable identifier (host_1, email_1, token_1).
- Same secret always maps to the same placeholder within a run (enables stable citations).
- Placeholder map lives only in the `Redactor` instance, in memory; never persisted or exposed.
- Patterns applied in order of specificity (most specific first): DB URLs, tokens, then hosts/emails/IPs.

```python
class Redactor(BaseModel):
    placeholder_map: Dict[str, str] = {}  # {original: placeholder}
    reverse_map: Dict[str, str] = {}  # {placeholder: original} (for internal use only)
    counters: Dict[str, int] = field(default_factory=lambda: {"host": 0, "email": 0, "token": 0, ...})
    
    def redact_line(self, line: LogLine) -> LogLine:
        """Apply redaction in order of specificity."""
        redacted_text = line.original_text
        
        # Order: most specific patterns first (DB URLs, tokens, then broad patterns)
        for pattern_name, pattern in self.PATTERNS_ORDERED:
            for match in pattern.finditer(redacted_text):
                secret = match.group(0)
                if secret not in self.placeholder_map:
                    self.counters[pattern_name] += 1
                    placeholder = f"{pattern_name}_{self.counters[pattern_name]}"
                    self.placeholder_map[secret] = placeholder
                    self.reverse_map[placeholder] = secret
                redacted_text = redacted_text.replace(secret, self.placeholder_map[secret])
        
        line.redacted_text = redacted_text
        return line
```

**Output display:**
- User sees redacted_text by default (no secrets in terminal or --json output).
- Original line numbers are used (line_id) for citations and output.
- Citations include snippets from the redacted version (what the LLM saw).

**Citation validation:**
- Citations validated against the redacted version of the line.
- Snippet must appear exactly in redacted_text.

**Leak tests:** `tests/test_redaction_leaks.py`
- Unit tests with real token formats: AWS keys (AKIA...), JWTs (ey...), DB URLs (postgresql://user:pass@host), Slack tokens (xoxb-...), env assignments (VAR=...).
- Each pattern must have 3–5 test cases; all must pass before launch.

---

## AD-7: Eval Harness

**Binds:** Folder structure for cases, the runner, scoring rules, and what runs in CI vs. on demand.

**Folder structure:**
```
eval/
├── cases/
│   ├── 01_loghub_db_timeout/
│   │   ├── input.log  # raw log file
│   │   └── ground_truth.yaml  # {expected_cause, cause_type (direct/temporal), evidence_lines: [line_ids]}
│   ├── 02_ci_pytest_failure/
│   │   ├── input.log
│   │   └── ground_truth.yaml
│   ├── ...
│   ├── 28_healthy_logs/
│   │   ├── input.log
│   │   └── ground_truth.yaml  # {expected_status: "nothing_unusual"}
│   └── 30_misleading_timeout/
│       ├── input.log
│       └── ground_truth.yaml  # {expected_cause, expected_unknowns: ["cause may be outside logs"]}
├── runner.py  # eval script
├── scorer.py  # scoring logic
└── results/
    └── {date}_{model}_{results}.json  # {correct_count, false_positives, etc.}
```

**Ground truth format** (`ground_truth.yaml`):
```yaml
status: "ok"  # or "nothing_unusual", "not_enough_signal"
cause: "OOM after memory spike"
cause_keywords: ["memory", "oom", "killed"]  # for fuzzy matching if exact cause differs
cause_type: "direct"  # or "temporal"
evidence_line_ids: [45, 47, 52]  # lines the cause is evident from
expected_unknowns:  # optional; if not specified, unknowns scored separately
  - "logs cover only 2 minutes"
```

**Scoring rules**:
```python
def score(actual_summary, ground_truth):
    """Correct = right cause AND all citations valid. Unknowns scored separately."""
    score = {
        "correct": False,
        "citations_valid": True,
    }
    
    if ground_truth["status"] == "nothing_unusual":
        if actual_summary.status == "nothing_unusual":
            score["correct"] = True
        elif actual_summary.status == "ok":
            # False positive: healthy logs labeled as incident
            return {"correct": False, "false_positive": True}
    
    elif actual_summary.status == "ok":
        if actual_summary.hypotheses:
            hyp = actual_summary.hypotheses[0]
            
            # Cause match: exact match or keyword match
            cause_match = (
                hyp.cause == ground_truth["cause"] or
                any(kw in hyp.cause.lower() for kw in ground_truth.get("cause_keywords", []))
            )
            
            # Citations valid: all citations pass validation
            citations_valid = all(
                citation_is_valid(c, log_lines) for c in hyp.citations
            )
            
            score["correct"] = cause_match and citations_valid
            score["citations_valid"] = citations_valid
    
    return score
```

**CI vs. on-demand:**
- **CI (every push):** Deterministic tests only: redaction leaks, citation validator unit tests, template mining, anomaly detection, refuse-to-guess logic. No LLM calls.
- **On-demand / before release:** Full eval set with LLM calls (20–30 cases). Takes longer, needs API key or Ollama running. Generates a report with accuracy, false-positive rate, per-model results.

---

## AD-8: Repository Folder Structure

```
logtldr/
├── src/
│   └── logtldr/  # package root (setuptools src layout)
│       ├── __init__.py
│       ├── main.py  # entry point, CLI parsing
│       ├── ingest.py  # read logs
│       ├── parse.py  # parse lines into fields
│       ├── redaction.py  # secret redaction
│       ├── drain_wrapper.py  # drain3 integration (message field only)
│       ├── anomaly_detector.py  # anomaly detection
│       ├── refuse_gate.py  # refuse-to-guess logic
│       ├── llm_gateway.py  # LiteLLM wrapper, --local enforcement
│       ├── citation_validator.py  # citation verification
│       ├── rules_engine.py  # evidence typing, confidence capping, unknowns
│       ├── renderer.py  # output formatting (terminal, --json)
│       ├── models.py  # Pydantic data models (BaseModel)
│       ├── config.py  # configuration, env vars, defaults
│       ├── direct_causes.yaml  # direct-cause catalog (package data)
│       └── integrations/  # v1.1+ extension points
│           ├── base.py  # Renderer ABC
│           ├── github_action.py  # (not implemented; interface only)
│           └── multi_pod.py  # (not implemented; interface only)
├── tests/
│   ├── test_parse.py  # parsing unit tests
│   ├── test_redaction.py  # redaction logic
│   ├── test_redaction_leaks.py  # AWS keys, JWTs, DB URLs, Slack tokens
│   ├── test_drain.py  # template mining + masking
│   ├── test_anomaly.py  # anomaly type detection
│   ├── test_citation_validator.py  # citation verification
│   ├── test_rules_engine.py  # evidence typing, confidence capping
│   ├── test_llm_gateway_local.py  # two network tests (non-localhost error, blocked network succeeds)
│   ├── test_end_to_end.py  # integration tests
│   └── conftest.py  # pytest fixtures
├── eval/
│   ├── cases/  # 25–30 eval cases with ground_truth.yaml (see AD-7)
│   ├── runner.py  # eval harness (runs cases, calls logtldr --json)
│   ├── scorer.py  # scoring logic (cause_keywords, citations, false positives)
│   └── results/  # eval results per date/model
├── README.md  # usage, examples, model selection
├── PRD.md  # product requirements
├── ARCHITECTURE.md  # this file
├── pyproject.toml  # setuptools, dependencies, package-data
├── .github/workflows/
│   └── ci.yml  # GitHub Actions v4+: lint, pytest, type-check (deterministic only)
├── .gitignore
└── poetry.lock / requirements-dev.txt  # optional
```

---

## AD-9: Testing Strategy

**Binds:** Unit tests per module, leak tests, network-blocked --local test, all in CI.

**Unit tests per module:**
- `test_parse.py`: JSON, plaintext, pytest, Docker, npm formats; edge cases (malformed timestamps, missing fields).
- `test_redaction.py`: Stable placeholder mapping, same secret → same placeholder, no leaks in redacted output.
- `test_drain.py`: Template mining with drain3; verify template IDs are stable.
- `test_anomaly.py`: Anomaly detection (new template, spike, first error, stack traces).
- `test_refuse_gate.py`: No anomalies → "nothing_unusual"; too little data → "not_enough_signal" (< 50 lines or < 30% parseable).
- `test_citation_validator.py`: Citation passes if line exists + snippet matches exactly in redacted version; fails otherwise.
- `test_rules_engine.py`: Evidence typing (direct vs. temporal downgrade); confidence capping; rule-based unknowns.
- `test_llm_gateway.py`: LiteLLM integration, JSON parsing, retry on failure.

**Redaction leak tests** (`test_redaction_leaks.py`):
- Real token formats: AWS keys (`AKIA...`), JWTs (three base64 sections), DB URLs (`postgresql://user:pass@host`), Slack tokens (`xoxb-...`), env var assignments (`TOKEN=...`).
- Each pattern has 3–5 test cases; all must pass before launch.

**Network enforcement tests** (`test_llm_gateway_local.py`):
```python
def test_local_flag_rejects_non_localhost():
    """Test (a): --local with non-localhost api_base → error."""
    config = Config(
        model="gpt-3.5-turbo",
        api_base="https://api.openai.com/v1",  # external
        local=True
    )
    with pytest.raises(ValueError, match="--local mode requires localhost"):
        LLMGateway(config)

def test_local_mode_with_blocked_network_succeeds():
    """Test (b): --local with blocked network succeeds against mocked local server."""
    import pytest_socket
    
    config = Config(
        model="ollama/mistral",
        api_base="http://localhost:11434",
        local=True
    )
    gateway = LLMGateway(config)
    
    # Mock Ollama response on localhost
    with patch('litellm.completion') as mock_llm:
        mock_llm.return_value = Mock(choices=[Mock(message=Mock(content='{"status": "ok"}'))])
        
        # Block all non-localhost network access
        with pytest_socket.disable_socket():
            # Allow localhost
            pytest_socket.socket_allow_hosts(['localhost', '127.0.0.1', '::1'])
            
            # This should succeed (using mocked localhost)
            result = gateway.call_summarize(compressed_view, LLMResponse)
            assert result.status == "ok"
```

**CI workflow** (`.github/workflows/ci.yml`, deterministic tests only):
```yaml
name: CI
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with: { python-version: "3.11" }
      - run: pip install -e . && pip install pytest pytest-cov pytest-socket
      - run: pytest tests/ --cov=src/logtldr --cov-report=term -v
      - run: ruff check src/logtldr tests/
      - run: mypy src/logtldr
```

(Full eval with LLM calls: separate `eval.yml` workflow, runs on demand or before release.)

---

## AD-10: Extension Points for v1.1+

**No code now; interfaces only.** These are locked in so v1.1 can plug in without refactoring.

**Renderer interface** (`src/renderers/base.py`):
```python
class Renderer(ABC):
    @abstractmethod
    def render(self, summary: FinalSummary) -> str:
        """Convert FinalSummary to output string (terminal, json, markdown, etc.)."""
        pass

# Current implementations:
class TerminalRenderer(Renderer): ...
class JSONRenderer(Renderer): ...

# v1.1 adds:
class MarkdownRenderer(Renderer): ...
```

**GitHub Action interface** (`src/integrations/github_action.py`):
```python
class GitHubAction:
    """v1.1 feature: post summary as comment on failed CI runs."""
    def __init__(self, token: str):
        pass
    
    def fetch_run_logs(self, run_id: str) -> str:
        """Get logs for a GitHub Actions run."""
        pass
    
    def post_comment(self, pr: int, summary: FinalSummary) -> None:
        """Post a comment on a PR."""
        pass
```

**Multi-pod merger interface** (`src/multi_pod.py`):
```python
class MultiPodMerger:
    """v1.1 feature: merge logs from multiple Kubernetes pods into one timeline."""
    def merge(self, pod_logs: Dict[str, str]) -> Tuple[LogLine[], str]:
        """Merge logs, return unified timeline and metadata."""
        pass
```

---

## Summary of Key Decisions

| Decision | Rationale |
|----------|-----------|
| Trust-by-code paradigm | LLM proposes, code enforces; prevents hallucinations from reaching users. |
| Deterministic citation validation | Snippet + line ID matching is bulletproof; no semantic matching. |
| Code-enforced confidence capping | Temporal evidence always capped at medium confidence, never left to LLM. |
| Stable placeholder redaction | Same secret always maps to the same placeholder, enabling stable citations. |
| Compressed view with token budget | Keeps 100k-line inputs within LLM context limits and <30s latency. |
| --local as default (no API key) | Privacy-first: external LLM access is explicit opt-in, not default. |
| Deterministic tests in CI, full eval on demand | Fast feedback loop for developers; expensive LLM calls only before release. |
| One direct-cause catalog (YAML) | Easy to extend, contributor-friendly, tested, grows from eval failures. |
| Separate hypotheses[] but show top only | Schema supports ranking for v1.1; MVP shows one, no confusion. |

---

## Open Questions

[ASSUMPTION: Compressed view token budget of 2k–4k tokens is sufficient for 100k-line inputs with Ollama; if local models run out of context, we'll document trade-offs and suggest API models.]

[ASSUMPTION: Drain3's default parameters (depth=4, st=0.5) work well enough for diverse log formats; will calibrate in week 1 if false-positive template rate is high.]

[ASSUMPTION: 30-second latency target is achievable for API models on 100k-line inputs; local models will be slower and reported honestly.]
