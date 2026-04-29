import time
from typing import Sequence

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    SpanExporter,
    SpanExportResult,
)


class FakeExporter(SpanExporter):
    """Custom exporter to trigger serialization logic without network I/O."""

    def export(self, spans: Sequence) -> SpanExportResult:
        print("[EXPORT] Number of spans:", len(spans), flush=True)
        for span in spans:
            print("[EXPORT] Span name:", span.name, flush=True)
            print("[EXPORT] Span attributes:", dict(span.attributes), flush=True)
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        print("[EXPORT] Shutdown exporter", flush=True)


def test_opentelemetry_tracing():
    resource = Resource(attributes={"service.name": "my-test-service"})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    exporter = FakeExporter()
    span_processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(span_processor)

    tracer = trace.get_tracer(__name__)

    with tracer.start_as_current_span("test-span") as span:
        span.set_attribute("custom.attribute", "test-value")
        span.add_event("test event")
        print("Created first span", flush=True)

    for i in range(3):
        with tracer.start_as_current_span(f"batch-span-{i}") as span:
            span.set_attribute("iteration", i)
            span.add_event(f"event-{i}")
            print(f"Generated span {i}", flush=True)
        time.sleep(0.1)

    time.sleep(1)
    print("Test complete. All spans processed through fake exporter.", flush=True)


if __name__ == "__main__":
    print("=== opentelemetry_test ===", flush=True)
    test_opentelemetry_tracing()
