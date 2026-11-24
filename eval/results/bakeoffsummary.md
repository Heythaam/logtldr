# LT-23 Local Model Bake-off Results

**Date:** 2026-11-22
**Hardware:** Intel i5-13th gen, RTX 2050 (4 GB VRAM), 16 GB RAM
**Ollama split:** 58 % CPU / 42 % GPU
**Eval cases:** 01-20 (20 total)

## Token-budget sweep

| Budget | Cases passing | Notes |
|--------|--------------|-------|
| 2000   | 16/19 (84.2 %) | Cases 01 & 02 return `not_enough_signal` — large logs truncated too aggressively |
| 3000   | 18/19 (94.7 %) | Cases 01 & 02 now pass; sweet spot for consumer hardware |
| 5000   | hangs | Compressed view + system prompt (~6 k tokens) exceeds Ollama's default 4096 context window |

> `num_ctx` is set to 8192 by Config, but the 5000-token view still caused multi-minute stalls on this hardware.

## Model comparison (budget = 2000 tokens)

| Metric | llama3.1:8b | qwen2.5:7b |
|--------|-------------|------------|
| Accuracy | 84.2 % | 84.2 % |
| False positives | 0 | 0 |
| JSON validity | 100 % | 100 % |
| Avg latency / case | ~55 s | ~46 s |

Both models scored identically on accuracy. **qwen2.5:7b** is chosen as the
baseline because it is ~17 % faster per case with the same accuracy and zero
JSON parse errors.

## Chosen defaults

| Setting | Local (`ollama/`) | Cloud API |
|---------|------------------|-----------|
| Model | `ollama/qwen2.5:7b` | `gpt-4o-mini` |
| Token budget | 3000 | 5000 |
| `num_ctx` | 8192 | n/a |

## Reproduction

```bash
# pull models
ollama pull qwen2.5:7b
ollama pull llama3.1:8b

# run bake-off
cd logtldr
python eval/bakeoff.py          # uses DEFAULT_MODELS
LOGTLDR_VIEW_BUDGET=3000 python eval/bakeoff.py   # explicit budget
```