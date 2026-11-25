# logtldr

Thousands of log lines → one-screen incident summary.

`logtldr` is a CLI tool for on-call engineers. Pipe noisy logs into it and get back a structured summary: what happened, in what order, the probable cause, and what to check next. The AI proposes, plain code verifies — every citation is checked against the original lines, confidence is capped by deterministic rules, and secrets are stripped before anything reaches a model.

```
kubectl logs my-pod --since=1h | logtldr
```

<!-- TODO: replace with demo GIF (LT-42) -->

## Install

```bash
pip install logtldr
```

Requires Python 3.11+. For local-only mode (the default), install [Ollama](https://ollama.com) and pull a model:

```bash
ollama pull llama3.1:8b
```

## Quick Start

**Summarize a log file:**

```bash
logtldr /var/log/app/error.log
```

**Pipe from kubectl:**

```bash
kubectl logs deploy/api-server --since=1h | logtldr
```

**Diagnose a failed CI run:**

```bash
gh run view <run-id> --log-failed | logtldr
```

## Output Formats

**Terminal (default)** — a one-screen summary with verdict, timeline, citations, and unknowns:

```bash
logtldr app.log
```

**JSON** — machine-readable output for scripting and pipelines:

```bash
logtldr --json app.log | jq '.hypotheses[0].cause'
```

## How It Works

```
Logs in → parse → redact secrets → mine templates (Drain3)
       → detect anomalies → refuse-to-guess gate
       → LLM (structured output) → validate citations
       → cap confidence by rules → render summary
```

1. **Parse** — auto-detects JSON lines, plaintext, pytest, Docker, npm, and GitHub Actions formats.
2. **Redact** — strips AWS keys, JWTs, database URLs, Slack tokens, IPs, emails, and env vars before any LLM call. Stable placeholders (`host_1`, `token_1`) preserve structure.
3. **Template mining** — Drain3 collapses repeated patterns so the model sees structure, not noise.
4. **Anomaly detection** — flags new templates, first errors, and frequency spikes.
5. **Refuse gate** — healthy logs get "nothing unusual" with no LLM call. Too little data gets "not enough signal."
6. **LLM** — receives a compressed view (templates + anomalies + sampled lines) under a strict token budget, returns structured JSON validated by Pydantic.
7. **Citation check** — every claim must cite an exact log line. Code verifies the quote matches. A correct cause with invalid citations counts as wrong.
8. **Rules engine** — confidence can only go down, never up. Evidence is typed (direct, correlated, inferred). Unknowns come from rules, not the model.

## Model Selection

logtldr uses [LiteLLM](https://github.com/BerriAI/litellm) for model routing. Any supported provider works.

### Bake-Off Results

Evaluated on 20 cases (Kubernetes, CI/CD, infrastructure): 18 diagnosis, 1 healthy-log, 1 cause-outside-logs. "Correct" means the top hypothesis matches the expected cause **and** all citations pass the validator.

| Model | Accuracy (in-log) | False Positives | Avg Latency | Cost / Summary |
|---|---|---|---|---|
| `ollama/qwen2.5:7b` (local) | 83% (15/18) | 0 | 46.2 s | free |
| `ollama/llama3.1:8b` (local) | 83% (15/18) | 0 | 55.2 s | free |

Both models correctly refused to diagnose the healthy log (0 false positives) and correctly returned "not enough signal" on the misleading-timeout case (0 fabricated causes). JSON output was valid 100% of the time.

> Numbers sourced from `eval/results/*.json`. Accuracy is measured on in-log diagnosis cases only.
> API model results will be added once cloud evals are run.

### Using a Specific Model

```bash
# Local (default) — no data leaves your machine
logtldr app.log

# Explicit local model
logtldr --model ollama/llama3.1:8b app.log

# Cloud model — requires explicit opt-in
logtldr --model groq/llama-3.1-70b-versatile --yes app.log

# Or set the env var once
export LOGTLDR_CONFIRM_API=1
logtldr --model gemini/gemini-1.5-flash app.log
```

Provider prefixes: `ollama/`, `groq/`, `gemini/`, `openrouter/`, `anthropic/`.

## Local Mode

**Local is the default.** When you run `logtldr` without `--model`, it uses `ollama/llama3.1:8b` on `localhost:11434`. No data leaves your machine.

**Works fully offline.** The tokenizer used to fit logs into the model's context ships with the install (via `litellm`), so logtldr never downloads anything at runtime. If it can't be loaded for any reason, logtldr falls back to approximate token counts and says so on stderr. This is tested in CI with all network sockets blocked.


**Tracing is off unless you turn it on.** For debugging cloud runs, logtldr can send
traces to [Langfuse](https://langfuse.com). It only does so when you opt in, never in
local mode, and it tells you on stderr when it's on:

    pip install "logtldr[tracing]"
    export LOGTLDR_TRACING=1     # plus your usual LANGFUSE_* keys

Traces contain the redacted prompt and the model's response, never your original log lines.

To use a cloud model, you must explicitly confirm:

```bash
logtldr --model groq/llama-3.1-70b-versatile --yes app.log
```

Or set the environment variable:

```bash
export LOGTLDR_CONFIRM_API=1
```

Without `--yes` or the env var, logtldr prints an error and exits. It never prompts interactively — piped input never blocks.

| Flag / Env | Effect |
|---|---|
| _(none)_ | Local mode via Ollama |
| `--local` | Force local even with `--model` |
| `--model ollama/*` | Always local |
| `--model groq/*` (etc.) | Requires `--yes` or `LOGTLDR_CONFIRM_API=1` |

## No Secrets Are Sent

Secrets are redacted **before** anything reaches a model — local or cloud. The redactor covers:

- AWS access keys (`AKIA...`)
- JWTs
- Database URLs (`postgresql://user:pass@...`)
- Slack tokens (`xoxb-...`)
- Environment variable assignments (`SECRET_KEY=...`)
- IPv4 and IPv6 addresses
- Email addresses

Stable placeholders (`token_1`, `host_1`, `email_1`) replace each secret consistently within a run, so the model can still reason about structure without seeing real values.

### Verified by Tests

Redaction leak tests run in CI on every push. Each pattern has 3+ test cases with real token formats. The tests assert that no secret appears in `redacted_text` after the redactor runs.

See: [`tests/test_redaction_leaks.py`](tests/test_redaction_leaks.py)

Additionally, the `--local` enforcement is tested with network sockets blocked (`pytest-socket`), ensuring local mode cannot accidentally reach an external API.

See: [`tests/test_llm_gateway_local.py`](tests/test_llm_gateway_local.py)

## Roadmap (v1.1)

- **Markdown renderer** (`--markdown`) for pasting into Slack / Notion
- **GitHub Action** — run logtldr automatically on failed CI
- **Multi-pod mode** — merge logs from multiple pods before summarizing
- **`--verbose`** — show the full pipeline trace
- **Slack bot** — `/logtldr` in a channel
- **Web UI** — upload logs in the browser

## Development

```bash
git clone https://github.com/Heythaam/logtldr.git
cd logtldr
pip install -e ".[dev]"
pytest
```

CI runs `pytest`, `ruff`, and `mypy` on every push (Python 3.11 and 3.12).

## License

MIT