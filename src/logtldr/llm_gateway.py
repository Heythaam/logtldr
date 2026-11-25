"""LiteLLM integration (AD-4)."""

import importlib.util
import os
import sys
from typing import Any, TypeVar
from urllib.parse import urlparse

import httpx
import litellm
from pydantic import BaseModel

from logtldr.config import Config
from logtldr.models import CompressedView
from logtldr.prompt import build_messages

T = TypeVar("T", bound=BaseModel)

TRACING_ENV = "LOGTLDR_TRACING"
_LANGFUSE = "langfuse"


def tracing_requested() -> bool:
    """True if the user explicitly opted into Langfuse tracing."""
    return os.environ.get(TRACING_ENV, "").strip().lower() in {"1", "true", "yes"}


def _langfuse_installed() -> bool:
    return importlib.util.find_spec(_LANGFUSE) is not None


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

        self.tracing = self._configure_tracing()

    def _configure_tracing(self) -> bool:
        """Turn Langfuse tracing on only if the user opted in (LT-47).

        Tracing sends redacted prompts and responses to Langfuse, so it needs
        an explicit LOGTLDR_TRACING=1, never runs in local mode, and needs the
        optional extra (pip install "logtldr[tracing]"). Langfuse is always
        removed from LiteLLM's callbacks first, because they are process-wide
        and could be left over from earlier code; other callbacks are kept.
        """
        enable = False
        if tracing_requested():
            if self.local:
                print(
                    f"logtldr: {TRACING_ENV} ignored in local mode; "
                    "no data leaves your machine.",
                    file=sys.stderr,
                )
            elif not _langfuse_installed():
                print(
                    f"logtldr: {TRACING_ENV}=1 but langfuse is not installed; "
                    'run: pip install "logtldr[tracing]". Tracing disabled.',
                    file=sys.stderr,
                )
            else:
                enable = True
                print(
                    "logtldr: tracing on; redacted prompts and responses "
                    "are sent to Langfuse.",
                    file=sys.stderr,
                )

        for attr in ("success_callback", "failure_callback"):
            callbacks = [cb for cb in (getattr(litellm, attr) or []) if cb != _LANGFUSE]
            if enable:
                callbacks.append(_LANGFUSE)
            setattr(litellm, attr, callbacks)
        return enable

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
                    # Ollama: skip json_schema (always fails), go straight to JSON mode
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
                        f"LLM returned invalid JSON after 3 attempts: {exc}"
                    ) from exc
                # Feed the bad reply back so the LLM can self-correct
                if raw_text is not None:
                    messages = messages + [
                        {"role": "assistant", "content": raw_text},
                        {"role": "user", "content": "That was not valid JSON. Return only valid JSON matching the schema."},
                    ]

        # Unreachable, but keeps mypy happy
        raise RuntimeError("LLM returned invalid JSON after 3 attempts")


    
    def _get_ollama_params(self) -> dict[str, Any]:
        """Ollama-specific parameters."""
        if "ollama" in self.model:
            return {"num_ctx": self.config.num_ctx}
        return {}