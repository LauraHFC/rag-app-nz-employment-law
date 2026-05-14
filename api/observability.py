# api/observability.py — Langfuse client factory + @observe re-export
#
# Sprint 5: minimum-viable observability. Langfuse v4 compatible.
#
# Design:
#   - Initialises Langfuse at import time using env vars (v4 reads them automatically).
#   - Fails open: if LANGFUSE_PUBLIC_KEY is absent or init raises, a no-op @observe
#     decorator is substituted — production answers are never broken by Langfuse.
#   - Re-exports `observe` and `get_trace_id()` for use in pipeline modules.
#
# Env vars (all optional — omit for local dev without Langfuse):
#   LANGFUSE_PUBLIC_KEY   — pk-lf-...
#   LANGFUSE_SECRET_KEY   — sk-lf-...
#   LANGFUSE_HOST         — default https://cloud.langfuse.com
#   APP_ENV               — "dev" | "prod" (default "dev")

from __future__ import annotations

import functools
import logging
import os
from typing import Any, Callable, TypeVar

log = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# ── Attempt Langfuse initialisation ───────────────────────────────────────────

_langfuse_enabled = False
_lf_observe: Any = None
_lf_get_client: Any = None

try:
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")

    if public_key and secret_key:
        # v4: observe and get_client live at the top-level package.
        # Langfuse v4 reads LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY / LANGFUSE_HOST
        # from env automatically — no explicit constructor call needed.
        from langfuse import observe as _lf_observe_import, get_client as _lf_get_client_import

        _lf_observe = _lf_observe_import
        _lf_get_client = _lf_get_client_import
        _app_env = os.environ.get("APP_ENV", "dev")
        _langfuse_enabled = True

        host = os.environ.get("LANGFUSE_HOST", "https://cloud.langfuse.com")
        log.info("[observability] Langfuse v4 enabled (host=%s env=%s)", host, _app_env)
    else:
        log.info("[observability] LANGFUSE_PUBLIC_KEY not set — tracing disabled (no-op mode)")

except Exception as exc:
    log.warning("[observability] Langfuse init failed — tracing disabled: %s", exc)
    _langfuse_enabled = False


# ── Public API ────────────────────────────────────────────────────────────────

def observe(**kwargs: Any) -> Callable[[F], F]:
    """
    Decorator that instruments a function as a Langfuse span.

    Usage:
        from api.observability import observe

        @observe()
        def my_function(...): ...

        @observe(as_type="generation")
        def generate(...): ...

    Falls back to a transparent no-op if Langfuse is disabled.
    """
    if _langfuse_enabled and _lf_observe is not None:
        return _lf_observe(**kwargs)  # type: ignore[return-value]

    # No-op decorator
    def _noop(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: Any, **kw: Any) -> Any:
            return fn(*args, **kw)
        return wrapper  # type: ignore[return-value]

    return _noop


def get_trace_id() -> str | None:
    """
    Return the current Langfuse trace ID, or None if tracing is disabled.

    Call this at the end of a request handler (inside or after an @observe
    decorated function) to inject the trace ID into the API response.
    """
    if not _langfuse_enabled or _lf_get_client is None:
        return None
    try:
        return _lf_get_client().get_current_trace_id()
    except Exception:
        return None


def flush() -> None:
    """Flush pending Langfuse events. Call in shutdown handlers or tests."""
    if _langfuse_enabled and _lf_get_client is not None:
        try:
            _lf_get_client().flush()
        except Exception:
            pass
