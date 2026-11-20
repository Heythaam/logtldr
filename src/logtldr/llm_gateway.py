"""LiteLLM integration (AD-4)."""

import json
from typing import Any, Type
from urllib.parse import urlparse

import httpx
import litellm
from pydantic import BaseModel, ValidationError

from logtldr.config import Config
from logtldr.models import Anomaly, CompressedView, LogLine, Template


def build_compressed_view(
    lines: list[LogLine], templates: list[Template], anomalies: list[Anomaly]
) -> CompressedView:
    """Helper to build the view."""
    return CompressedView(
        templates=[{"template_id": t.template_id, "pattern": t.pattern, "count": t.count} for t in templates],
        anomalies=anomalies,
        timeline_candidates=[],
        sample_lines=[{"line_id": line.line_id, "redacted_text": line.redacted_text} for line in lines[:5]],
        statistics={"total_lines": len(lines)},
        metadata={"log_format": lines[0].source_format if lines else "unknown"},
        estimated_tokens=len(lines) * 10,
    )


class LLMGateway:
    """Handles all LLM calls, enforcing localhost restrictions."""

    def __init__(self, config: Config):
        self.config = config
        self.model = config.model
        self.api_base = config.api_base
        self.local = config.local

        # 1. If --local, validate api_base is localhost
        if self.local:
            self._validate_localhost_endpoint(self.api_base)

        # 2. If no API key and Ollama is not reachable, raise a clear error
        if self.local and not config.api_key:
            self._check_ollama_reachable(self.api_base)

        # Set LiteLLM globals (as per AD-4)
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

    def _check_ollama_reachable(self, api_base: str) -> None:
        """Ensure Ollama is actually running before we try to use it."""
        try:
            # Quick 1-second timeout to check if the server is up
            httpx.get(f"{api_base}/api/tags", timeout=1.0)
        except httpx.RequestError:
            raise ValueError(
                f"Ollama not running on {api_base}. "
                f"Check your config (api_base={api_base}) or provide an API key."
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
                        response_format={"type": "json_schema", "json_schema": {"name": "summary", "schema": schema.model_json_schema()}}
                    )
                    raw_text = response.choices[0].message.content  # type: ignore
                except Exception:
                    # Fallback: JSON mode + Pydantic validation
                    response = litellm.completion(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=self.config.temperature,
                        **self._get_ollama_params()
                    )
                    raw_text = response.choices[0].message.content  # type: ignore
                
                # Validate and parse
                if not raw_text:
                    raise ValueError("LLM returned empty response")
                return schema.model_validate_json(raw_text)
            
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                if attempt < 2:
                    # Feed validation error back in retry prompt
                    prompt += f"\n\n[Validation error: {str(e)[:200]}. Please return valid JSON.]"
                    continue
                else:
                    raise RuntimeError(
                        f"LLM returned invalid JSON after 3 attempts: {e}. "
                        f"Check model capacity (try a larger model or API model)."
                    )
        
        raise RuntimeError("Unreachable")

    def _get_ollama_params(self) -> dict[str, Any]:
        """Ollama-specific parameters."""
        if "ollama" in self.model:
            return {"num_ctx": self.config.num_ctx}
        return {}

    def _build_prompt(self, compressed_view: CompressedView) -> str:
        """Build the prompt sent to the LLM."""
        # Note: We will implement the exact prompt in a later ticket.
        return f"Analyze this log data and return JSON:\n{compressed_view.model_dump_json(indent=2)}"