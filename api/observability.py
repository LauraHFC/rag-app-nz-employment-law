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
_lf_propagate_attributes: Any = None  # v4: trace-level tag/metadata propagation

try:
    public_key = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
    secret_key = os.environ.get("LANGFUSE_SECRET_KEY", "")

    if public_key and secret_key:
        # v4: observe, get_client, and propagate_attributes live at the top-level
        # package. Langfuse v4 reads LANGFUSE_PUBLIC_KEY / LANGFUSE_SECRET_KEY /
        # LANGFUSE_HOST from env automatically — no explicit constructor needed.
        from langfuse import (
            observe as _lf_observe_import,
            get_client as _lf_get_client_import,
            propagate_attributes as _lf_propagate_attributes_import,
        )

        _lf_observe = _lf_observe_import
        _lf_get_client = _lf_get_client_import
        _lf_propagate_attributes = _lf_propagate_attributes_import
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


# ── Eval tag injection ────────────────────────────────────────────────────────
#
# Sprint 5 v2 eval runner (evals/run_eval.py) injects per-question tags via
# X-Eval-* HTTP headers. The request handler calls tag_eval_trace(headers) once
# inside the @observe scope; the helper attaches the tags + env=eval marker to
# the current trace so the Langfuse "env=eval" filter can isolate eval runs.
#
# Production requests carry no X-Eval-* headers and tag_eval_trace is a no-op.

_EVAL_HEADER_KEYS: dict[str, str] = {
    # HTTP header name (case-insensitive) → tag key on the trace
    "x-eval-run-id":           "eval_run_id",
    "x-eval-question-id":      "question_id",
    "x-eval-pipeline":         "pipeline",
    "x-eval-expected-outcome": "expected_outcome",
    "x-eval-difficulty":       "difficulty",
}


import contextlib
from typing import Iterator


@contextlib.contextmanager
def eval_trace_context(headers: Any) -> Iterator[bool]:
    """
    Context manager: inspect incoming HTTP headers; if any X-Eval-* header is
    present, open a Langfuse `propagate_attributes` scope that marks the trace
    (and every child span) with env=eval plus the supplied eval metadata.

    Why a context manager (not a one-shot call): Langfuse v4 propagates
    trace-level tags via OpenTelemetry baggage. The propagation only applies
    to spans created **inside** the context manager's `with` block. The route
    handler must therefore wrap its work in this context, like:

        with eval_trace_context(request.headers):
            result = agent_run(...)   # all @observe spans created here are tagged

    Args:
        headers: a mapping with case-insensitive .get() (e.g. starlette/fastapi
                 Request.headers, or a plain dict).

    Yields:
        True if eval tags were applied (eval request, tracing enabled),
        False otherwise (production request OR tracing disabled).

    Never raises — observability outages must not break production answers.
    Production requests (no X-Eval-* headers) get a transparent no-op context.
    """
    if not _langfuse_enabled or _lf_propagate_attributes is None:
        yield False
        return

    try:
        # Normalise: build {tag_key: value} from any present X-Eval-* headers.
        getter = getattr(headers, "get", None)
        if getter is None:
            yield False
            return

        collected: dict[str, str] = {}
        for hdr, tag_key in _EVAL_HEADER_KEYS.items():
            val = getter(hdr)
            if val:
                collected[tag_key] = val

        if not collected:
            yield False  # production request — no eval headers present
            return

        # tags for the filterable string markers in the Langfuse UI;
        # metadata for the same data in structured form (queryable later).
        tags_list = ["env=eval"] + [f"{k}={v}" for k, v in collected.items()]
        metadata: dict[str, str] = {"env": "eval", **collected}

        with _lf_propagate_attributes(tags=tags_list, metadata=metadata):
            yield True
            return

    except Exception as exc:
        # Fail open — never surface tracing errors to the request path.
        log.warning("[observability] eval_trace_context failed (non-fatal): %s", exc)
        yield False


def tag_eval_trace(headers: Any) -> bool:
    """
    DEPRECATED — kept for backward compatibility with call sites that haven't
    been migrated yet. This function CANNOT work in v4 because Langfuse trace
    tags must be set inside an OTel context-manager scope (propagate_attributes)
    that wraps the span-producing work. Calling it outside such a scope is a
    no-op. Use `eval_trace_context(headers)` as a `with`-block instead.

    Returns False unconditionally so existing callers don't see a misleading
    "True" result; they should be migrated.
    """
    return False


def tag_current_span(**attrs: Any) -> None:
    """
    Attach arbitrary key-value attributes to the *current* OpenTelemetry span.

    Sprint 5 v2 §6.D.1 cross-cut wants regeneration_count / banned_phrase_hits
    visible on the `generate` span in Langfuse. v4 doesn't expose a public
    "update current observation" API, so we set the attributes directly via
    OpenTelemetry — Langfuse's collector recognises the `langfuse.observation.*`
    namespace and surfaces them in the dashboard.

    Never raises.
    """
    if not _langfuse_enabled:
        return
    if not attrs:
        return
    try:
        # Lazy import — only paid for when tracing is on.
        from opentelemetry import trace as otel_trace_api

        current = otel_trace_api.get_current_span()
        if current is None or not current.is_recording():
            return

        # Langfuse expects observation metadata under the
        # `langfuse.observation.metadata.<key>` attribute namespace.
        for key, value in attrs.items():
            # OTel only accepts str / bool / int / float / sequences of those.
            # Coerce anything else to str so we never crash on weird types.
            if isinstance(value, (str, bool, int, float)):
                ser_value: Any = value
            elif isinstance(value, (list, tuple)):
                ser_value = [str(v) for v in value]
            else:
                ser_value = str(value)
            current.set_attribute(
                f"langfuse.observation.metadata.{key}", ser_value
            )
    except Exception as exc:
        log.warning("[observability] tag_current_span failed (non-fatal): %s", exc)


def flush() -> None:
    """Flush pending Langfuse events. Call in shutdown handlers or tests."""
    if _langfuse_enabled and _lf_get_client is not None:
        try:
            _lf_get_client().flush()
        except Exception:
            pass
