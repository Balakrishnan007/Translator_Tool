# -*- coding: utf-8 -*-
"""Langfuse tracing for every Anthropic API call.

Answers a question Anthropic's own console can never answer: not just when
and how much a call cost, but *what the app was doing* when it made that
call, which function, which segment, which language pair. Anthropic never
receives that context; it only exists in our code, so only our code can
attach it.

Setup: instruments the Anthropic SDK once via OpenTelemetry (every
client.messages.create call is automatically captured), then `traced()` is
used as a context manager around each call site to attach business-level
metadata on top of the automatic capture.
"""

from opentelemetry.instrumentation.anthropic import AnthropicInstrumentor
from langfuse import propagate_attributes

_instrumented = False


def init_tracing():
    """Idempotent. Safe to call from every module that makes API calls."""
    global _instrumented
    if _instrumented:
        return
    AnthropicInstrumentor().instrument()
    _instrumented = True


def flush_tracing():
    """Short-lived scripts (a one-off test, run_app.py exiting after one
    document) can exit before the background exporter has sent queued spans.
    Confirmed a real gap, not hypothetical: Langfuse's own docs flag this
    exact case. Call this once, right before the process ends."""
    if not _instrumented:
        return
    try:
        from langfuse import get_client
        get_client().flush()
    except Exception:
        pass


def traced(operation: str, **metadata):
    """Tags the Anthropic call(s) made inside this `with` block with which
    operation this is (e.g. "translate_segment", "detect_language") plus
    whatever business context is relevant (languages, segment id, use_glossary).
    Falls back to a no-op if Langfuse isn't configured (missing .env keys),
    so tracing can never be the reason a translation fails."""
    init_tracing()
    try:
        return propagate_attributes(tags=[operation], metadata=metadata)
    except Exception:
        from contextlib import nullcontext
        return nullcontext()
