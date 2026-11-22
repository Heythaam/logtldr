"""Langfuse tracing wrapper (LT-19)."""

import contextlib
from typing import Any, Generator

# We only import langfuse if tracing is enabled to avoid overhead in local mode.
_langfuse_client: Any = None


def init_tracer(local_mode: bool) -> None:
    """Initialize the Langfuse client if not in local mode."""
    global _langfuse_client
    if local_mode:
        return

    try:
        from langfuse import get_client
        _langfuse_client = get_client()
    except ImportError:
        print("Warning: langfuse package not found. Tracing disabled.")


@contextlib.contextmanager
def trace_span(name: str, **kwargs: Any) -> Generator[Any, None, None]:
    """Context manager for a trace span. Acts as no-op if tracing is disabled."""
    if _langfuse_client is None:
        yield None
        return

    try:
        with _langfuse_client.start_as_current_observation(as_type="span", name=name, **kwargs) as span:
            yield span
    except Exception:
        # Fallback if the SDK version is different or API fails
        yield None


def flush() -> None:
    """Flush traces to Langfuse (call at the end of execution)."""
    if _langfuse_client:
        _langfuse_client.flush()