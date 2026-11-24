# logtldr

Thousands of log lines → one-screen incident summary.

Pipe noisy logs in, get back what happened, why, and what to check next. Every claim is cited against the original lines — if the AI can't prove it from your logs, it doesn't say it.

```
kubectl logs my-pod --since=1h | logtldr
```

## Install

```bash
pip install logtldr
```

Requires Python 3.10+.

By default logtldr runs locally with [Ollama](https://ollama.com) — no data leaves your machine. To set it up:

```bash
ollama pull llama3.1:8b
```

## Usage

```bash
# Summarize a log file
logtldr /var/log/app/error.log

# Pipe from Docker
docker logs my-container 2>&1 | logtldr

# Pipe from kubectl
kubectl logs deploy/api-server --since=1h | logtldr

# Diagnose a failed CI run
gh run view <run-id> --log-failed | logtldr

# JSON output for scripting
docker logs my-container 2>&1 | logtldr --json | jq '.hypotheses[0].cause'
```

## What You Get

**In your terminal** — a colored, one-screen summary:

```
─────────────────── logtldr ───────────────────
Status: problem_detected
Verdict: Redis hit maxmemory limit, triggering OOM
         and dropping client connections.

Evidence:
  [1] line 42: "OOM command not allowed when used memory > 'maxmemory'"
  [2] line 38: "Client closed connection"

Timeline:
  → line 12: INFO Server started
  → line 38: WARNING Client closed connection
  → line 42: ERROR OOM command not allowed

Check next:
  → Increase maxmemory or add eviction policy
────────────────────────────────────────────────
```

**With `--json`** — structured output you can pipe into `jq`, send to Slack, or feed into other tools.

## Configuration

Save your preferred model and API keys so you don't have to type them every time.

```bash
# Set a default model
logtldr config set-model ollama/qwen2.5:7b

# Or use a cloud model
logtldr config set-model groq/llama-3.3-70b-versatile

# Save an API key (prompts securely, never shown in terminal)
logtldr config set-key groq

# See current config
logtldr config show

# Reset everything
logtldr config clear
```

Config is stored in `~/.config/logtldr/config.json` with restricted permissions.

## CLI Reference

```
logtldr [OPTIONS] [FILE]
```

| Option | Description |
|---|---|
| `FILE` | Log file to analyze. Omit to read from stdin (pipe). |
| `--model NAME` | LLM model to use (overrides saved config). |
| `--local` | Force local mode (Ollama only). |
| `--json` | Output as JSON instead of colored terminal summary. |
| `--yes` | Skip the cloud API confirmation prompt. |
| `--version` | Print version and exit. |
| `config show` | Show saved configuration. |
| `config set-model NAME` | Save a default model. |
| `config set-key PROVIDER` | Save an API key for a provider (groq, openai, anthropic, etc). |
| `config clear` | Delete saved configuration. |

## Using Cloud Models

By default, logtldr uses your local Ollama. To use a cloud model, you need two things:

**1. An API key** — set it as an environment variable or save it:

```bash
# Option A: environment variable
export GROQ_API_KEY=your-key-here

# Option B: save it (more convenient)
logtldr config set-key groq
```

**2. Explicit confirmation** — logtldr never sends logs to an external API without your say-so:

```bash
# Per-command
logtldr --model groq/llama-3.3-70b-versatile --yes app.log

# Or set once for the session
export LOGTLDR_CONFIRM_API=1
```

Supported providers: `ollama/`, `groq/`, `openai/`, `anthropic/`, `gemini/`, `openrouter/`, `xai/`.

## Privacy & Security

**Secrets are stripped before anything reaches a model** — local or cloud. The redactor catches AWS keys, JWTs, database URLs, Slack tokens, IP addresses, emails, and environment variable assignments. Each secret is replaced with a stable placeholder (`token_1`, `host_1`) so the model can still reason about structure.

Redaction is tested in CI with real token formats. See [`tests/test_redaction_leaks.py`](tests/test_redaction_leaks.py).

**Local mode is the default.** No flags needed, no data leaves your machine. Cloud mode requires `--yes` or `LOGTLDR_CONFIRM_API=1` — logtldr never prompts interactively, so piped input never blocks.

## How It Works

```
Logs in → parse → redact → template mining (Drain3)
       → anomaly detection → refuse gate
       → LLM → citation validation → rules engine → render
```

The pipeline is designed so the AI proposes and deterministic code verifies:

- **Parse** — auto-detects JSON, plaintext, pytest, Docker, npm, and GitHub Actions formats.
- **Redact** — strips secrets before any LLM call.
- **Template mining** — Drain3 collapses repeated patterns so the model sees structure, not noise.
- **Anomaly detection** — flags new templates, first errors, and frequency spikes.
- **Refuse gate** — healthy logs get "nothing unusual" with no LLM call. Too little data gets "not enough signal." No wasted tokens, no hallucinated problems.
- **LLM** — receives a compressed view under a strict token budget, returns structured JSON validated by Pydantic.
- **Citation check** — every claim must cite an exact log line. If the quote doesn't match, the hypothesis is thrown out.
- **Rules engine** — confidence can only go down, never up. Unknowns come from rules, not the model.

## Benchmarks

Evaluated on 20 cases (Kubernetes, CI/CD, infrastructure):

| Model | Accuracy | False Positives | Avg Latency | Cost |
|---|---|---|---|---|
| `ollama/qwen2.5:7b` | 83% (15/18) | 0 | 46s | free |
| `ollama/llama3.1:8b` | 83% (15/18) | 0 | 55s | free |

Both models correctly identified the healthy log (no false positives) and refused to guess on ambiguous cases. JSON output was valid 100% of the time.

## Development

```bash
git clone https://github.com/Heythaam/logtldr.git
cd logtldr
pip install -e ".[dev]"
pytest
```

205 tests cover the full pipeline. CI runs pytest, ruff, and mypy on every push.

## License

MIT