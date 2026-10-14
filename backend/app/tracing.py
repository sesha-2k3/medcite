"""Request tracing behind a small interface; Langfuse when keys are set, otherwise nothing.

One trace per request, one span per graph node, one generation per LLM call. Tracing fails
open: any error from the Langfuse SDK is logged once and that trace stops recording, so
tracing can never break an answer.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Protocol

from app.config import Settings
from app.models.llm import LLMResponse

logger = logging.getLogger(__name__)

MAX_OUTPUT_CHARS = 4000


class NodeSpan(Protocol):
    def record_llm_calls(self, calls: Sequence[LLMResponse]) -> None: ...


class Trace(Protocol):
    @property
    def trace_id(self) -> str: ...

    def node(self, name: str) -> contextlib.AbstractContextManager[NodeSpan]: ...

    def update(self, *, output: Any = None, metadata: Mapping[str, Any] | None = None) -> None: ...

    def end(self) -> None: ...


class Tracer(Protocol):
    def start(self, *, seed: str, name: str, user_id: str, input: Any) -> Trace: ...

    def shutdown(self) -> None: ...


class _NoopSpan:
    def record_llm_calls(self, calls: Sequence[LLMResponse]) -> None:
        return None


class NoopTrace:
    def __init__(self, trace_id: str) -> None:
        self._trace_id = trace_id

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @contextlib.contextmanager
    def node(self, name: str) -> Iterator[NodeSpan]:
        yield _NoopSpan()

    def update(self, *, output: Any = None, metadata: Mapping[str, Any] | None = None) -> None:
        return None

    def end(self) -> None:
        return None


class NoopTracer:
    """Without Langfuse the trace ID is the request ID, so logs and ``done`` still match."""

    def start(self, *, seed: str, name: str, user_id: str, input: Any) -> Trace:
        return NoopTrace(seed)

    def shutdown(self) -> None:
        return None


class _LangfuseSpan:
    def __init__(self, trace: _LangfuseTrace, span: Any) -> None:
        self._trace = trace
        self._span = span

    def record_llm_calls(self, calls: Sequence[LLMResponse]) -> None:
        for call in calls:
            self._trace.safe(lambda c=call: _generation(self._span, c))


class _LangfuseTrace:
    def __init__(self, trace_id: str, root: Any) -> None:
        self._trace_id = trace_id
        self._root = root
        self._broken = False

    @property
    def trace_id(self) -> str:
        return self._trace_id

    @property
    def broken(self) -> bool:
        return self._broken

    def safe(self, action: Any) -> Any:
        if self._broken:
            return None
        try:
            return action()
        except Exception as exc:
            self._broken = True
            logger.warning("trace_failed", extra={"trace_id": self._trace_id, "error": repr(exc)})
            return None

    @contextlib.contextmanager
    def node(self, name: str) -> Iterator[NodeSpan]:
        span = self.safe(lambda: self._root.start_observation(name=name, as_type="span"))
        try:
            yield _LangfuseSpan(self, span) if span is not None else _NoopSpan()
        finally:
            if span is not None:
                self.safe(span.end)

    def update(self, *, output: Any = None, metadata: Mapping[str, Any] | None = None) -> None:
        self.safe(lambda: self._root.update(output=output, metadata=dict(metadata or {})))

    def end(self) -> None:
        self.safe(self._root.end)


def _generation(parent: Any, call: LLMResponse) -> None:
    generation = parent.start_observation(
        name=f"llm:{call.role.value}",
        as_type="generation",
        model=call.model,
        output=call.text[:MAX_OUTPUT_CHARS],
        usage_details={"input": call.input_tokens, "output": call.output_tokens},
        cost_details={"total": float(call.cost_usd)},
        metadata={"role": call.role.value, "latency_ms": call.latency_ms},
    )
    generation.end()


class LangfuseTracer:
    def __init__(self, client: Any) -> None:
        self._client = client

    def start(
        self,
        *,
        seed: str,
        name: str,
        user_id: str,
        input: Any,
        metadata: Mapping[str, Any] | None = None,
    ) -> Trace:
        try:
            trace_id = str(self._client.create_trace_id(seed=seed))
            root = self._client.start_observation(
                name=name,
                as_type="span",
                trace_context={"trace_id": trace_id},
                input=input,
                metadata={"user_id": user_id, **(metadata or {})},
            )
        except Exception as exc:
            logger.warning("trace_start_failed", extra={"error": repr(exc)})
            return NoopTrace(seed)
        trace = _LangfuseTrace(trace_id, root)
        if hasattr(root, "update_trace"):
            trace.safe(lambda: root.update_trace(user_id=user_id, name=name))
        return trace

    def auth_ok(self) -> bool:
        """Whether the host accepts the keys; checked once, before anything is sent."""
        try:
            return bool(self._client.auth_check())
        except Exception as exc:
            logger.warning("langfuse_auth_failed", extra={"error": repr(exc)})
            return False

    def url(self, trace_id: str) -> str | None:
        try:
            return str(self._client.get_trace_url(trace_id=trace_id))
        except Exception:
            return None

    def shutdown(self) -> None:
        try:
            self._client.shutdown()
        except Exception as exc:
            logger.warning("tracer_shutdown_failed", extra={"error": repr(exc)})


def build_tracer(settings: Settings) -> Tracer:
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return NoopTracer()
    from langfuse import Langfuse

    options: dict[str, Any] = {
        "public_key": settings.langfuse_public_key,
        "secret_key": settings.langfuse_secret_key.get_secret_value(),
    }
    if settings.langfuse_host:
        accepted = inspect.signature(Langfuse).parameters
        options["base_url" if "base_url" in accepted else "host"] = settings.langfuse_host
    return LangfuseTracer(Langfuse(**options))
