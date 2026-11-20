"""Configuration models (AD-4)."""

from typing import Optional

from pydantic import BaseModel


class Config(BaseModel):
    """Configuration for the LLM Gateway."""
    model: str
    api_base: Optional[str] = None
    api_key: Optional[str] = None
    local: bool = True
    num_ctx: int = 8192
    temperature: float = 0.0