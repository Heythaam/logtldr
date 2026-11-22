"""Configuration models (AD-4)."""

from typing import Optional

from pydantic import BaseModel

# Baseline local model chosen by LT-23 bake-off.
DEFAULT_LOCAL_MODEL = "ollama/qwen2.5:7b"

# Token budgets: local models run with smaller context to stay responsive
# on consumer hardware (16 GB RAM, entry-level GPU).
VIEW_BUDGET_LOCAL = 3000
VIEW_BUDGET_CLOUD = 5000


class Config(BaseModel):
    """Configuration for the LLM Gateway."""
    model: str
    api_base: str
    api_key: Optional[str] = None
    local: bool = True
    num_ctx: int = 8192
    temperature: float = 0.0