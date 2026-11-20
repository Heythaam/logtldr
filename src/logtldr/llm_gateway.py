"""LiteLLM integration (AD-4)."""

import json
from typing import Any, Type
from urllib.parse import urlparse

import httpx
import litellm
from pydantic import BaseModel, ValidationError

from logtldr.config import Config
from logtldr.models import CompressedView


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
                # 1. Try json_schema first (OpenAI/Claude support)
                try:
                    response = litellm.completion(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=self.config.temperature,
                        response_format={
                            "type": "json_schema", 
                            "json_schema": {"name": "summary", "schema": schema.model_json_schema()}
                        }
                    )
                    raw_text = response.choices[0].message.content  # type: ignore
                except Exception:
                    # 2. Fallback: JSON mode + Pydantic validation (Ollama/older models)
                    response = litellm.completion(
                        model=self.model,
                        messages=[{"role": "user", "content": prompt}],
                        temperature=self.config.temperature,
                        **self._get_ollama_params()
                    )
                    raw_text = response.choices[0].message.content  # type: ignore
                
                if not raw_text:
                    raise ValueError("LLM returned an empty response.")

                # 3. Validate and parse
                return schema.model_validate_json(raw_text)
            
            except (json.JSONDecodeError, ValidationError, ValueError) as e:
                if attempt < 2:
                    # 4. Feed the validation error back into the prompt for the next attempt
                    prompt += f"\n\n[Validation error: {str(e)[:200]}. Please return valid JSON.]"
                    continue
                else:
                    # 5. Give up after 3 failures
                    raise RuntimeError(
                        f"LLM returned invalid JSON after 3 attempts: {e}. "
                        f"Check model capacity (try a larger model or API model)."
                    )
        
        raise RuntimeError("Unreachable")  # Satisfy mypy

    def _get_ollama_params(self) -> dict[str, Any]:
        """Ollama-specific parameters."""
        if "ollama" in self.model:
            return {"num_ctx": self.config.num_ctx}
        return {}

    def _build_prompt(self, compressed_view: CompressedView) -> str:
        """Build the prompt sent to the LLM."""
        # Note: We will implement the exact prompt in a later ticket.
        return f"Analyze this log data and return JSON:\n{compressed_view.model_dump_json(indent=2)}"