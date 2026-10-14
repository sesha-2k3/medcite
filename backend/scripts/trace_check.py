"""Send one test trace to Langfuse and print its link.

uv run python -m scripts.trace_check

Needs LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY (and LANGFUSE_HOST for a self-hosted or
regional server). The trace has a span and a generation, like a real request.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.config import get_settings
from app.models.llm import LLMResponse, LLMRole
from app.observability import configure_logging
from app.tracing import LangfuseTracer, NoopTrace, build_tracer


def main() -> None:
    settings = get_settings()
    configure_logging("WARNING")
    tracer = build_tracer(settings)
    if not isinstance(tracer, LangfuseTracer):
        print("tracing is off: set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY in backend/.env")
        return
    if not tracer.auth_ok():
        host = settings.langfuse_host or "the SDK default (EU cloud)"
        print(
            f"Langfuse rejected the keys at {host};LANGFUSE_HOST must match your project's region"
        )
        return
    trace = tracer.start(
        seed=uuid.uuid4().hex, name="trace-check", user_id="trace-check", input={"ok": True}
    )
    with trace.node("generate") as span:
        span.record_llm_calls(
            [
                LLMResponse(
                    role=LLMRole.GENERATOR,
                    model=settings.llm_model_generator,
                    text="{}",
                    input_tokens=10,
                    output_tokens=5,
                    latency_ms=100,
                    cost_usd=Decimal(0),
                )
            ]
        )
    trace.update(output={"ok": True})
    trace.end()
    tracer.shutdown()
    if isinstance(trace, NoopTrace) or getattr(trace, "broken", False):
        print("the Langfuse SDK raised an error; see the warning above")
        return
    print(f"trace {trace.trace_id} sent")
    print(tracer.url(trace.trace_id) or "(no trace URL returned)")


if __name__ == "__main__":
    main()
