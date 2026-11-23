"""LiteLLM integration (AD-4)."""

import json
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx
import litellm
from pydantic import BaseModel, ValidationError

from logtldr.config import Config
from logtldr.models import CompressedView
from logtldr.prompt import build_messages

T = TypeVar("T", bound=BaseModel)


class LLMGateway:
    """Handles all LLM calls, enforcing localhost restrictions."""

    def __init__(self, config: Config):
        self.config = config
        self.model = config.model
        self.api_base = config.api_base
        self.local = config.local

        # 1. If --local, validate api_base is localhost
        if self.local:
            if not self.api_base:
                raise ValueError("--local mode requires an api_base (e.g. http://localhost:11434).")
            self._validate_localhost_endpoint(self.api_base)

            # 2. If no API key and Ollama is not reachable, raise a clear error
            if not config.api_key:
                self._check_ollama_reachable(self.api_base)

        # Set LiteLLM globals (as per AD-4). Always assign: None resets any endpoint
        # left over from an earlier call, so LiteLLM routes by model prefix (xai/..., openai/...).
        litellm.api_base = self.api_base
        if config.api_key:
            litellm.api_key = config.api_key

        # NEW LANGFUSE LOGIC - DO NOT IMPORT LITELLM HERE
        if not self.local:
            try:
                litellm.success_callback = ["langfuse"]
                litellm.failure_callback = ["langfuse"]
            except Exception:
                pass

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




    def call_summarize(self, compressed_view: CompressedView, schema: type[T]) -> T:
        messages = build_messages(compressed_view)
        is_ollama = self.model.startswith("ollama/")

        for attempt in range(3):
            raw_text = None
            try:
                if is_ollama:
                    # Ollama: skip json_schema (it always fails), go straight to JSON mode
                    response = litellm.completion(
                        model=self.model,
                        messages=messages,
                        temperature=self.config.temperature,
                        **self._get_ollama_params(),
                    )
                    raw_text = response.choices[0].message.content
                else:
                    # 1. Try json_schema first (OpenAI/Claude support)
                    try:
                        response = litellm.completion(
                            model=self.model,
                            messages=messages,
                            temperature=self.config.temperature,
                            response_format={
                                "type": "json_schema",
                                "json_schema": {"name": "summary", "schema": schema.model_json_schema()}
                            }
                        )
                        raw_text = response.choices[0].message.content
                    except Exception:
                        # 2. Fallback: JSON mode + Pydantic validation (older models)
                        response = litellm.completion(
                            model=self.model,
                            messages=messages,
                            temperature=self.config.temperature,
                        )
                        raw_text = response.choices[0].message.content

                if not raw_text:
                    raise ValueError("Empty response from LLM")

                return schema.model_validate_json(raw_text)

            except Exception as exc:
                if attempt == 2:
                    raise RuntimeError(
                        f"LLM summarisation failed after 3 attempts: {exc}"
                    ) from exc

    def _get_ollama_params(self) -> dict[str, Any]:
        """Ollama-specific parameters."""
        if "ollama" in self.model:
            return {"num_ctx": self.config.num_ctx}
        return {}