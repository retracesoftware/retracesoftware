from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult, BatchSpanProcessor
from opentelemetry.sdk.resources import Resource
from typing import Sequence
import time

# Test metadata (used by run_all.py)
TAGS = ["fast", "tracing"]
TIMEOUT = 60


class FakeExporter(SpanExporter):
    """Custom exporter to trigger serialization logic without network I/O."""
    def export(self, spans: Sequence) -> SpanExportResult:
        print("[EXPORT] Number of spans:", len(spans))
        for span in spans:
            print("[EXPORT] Span name:", span.name)
            print("[EXPORT] Span attributes:", dict(span.attributes))
        return SpanExportResult.SUCCESS

    def shutdown(self) -> None:
        print("[EXPORT] Shutdown exporter")


def test():
    """Main test entry point."""
    # Set up tracing provider with resource
    resource = Resource(attributes={"service.name": "my-test-service"})
    provider = TracerProvider(resource=resource)
    trace.set_tracer_provider(provider)

    # Use fake exporter instead of real OTLP to avoid network dependencies
    exporter = FakeExporter()
    span_processor = BatchSpanProcessor(exporter)
    provider.add_span_processor(span_processor)

    # Get tracer and create spans
    tracer = trace.get_tracer(__name__)

    # First span
    with tracer.start_as_current_span("test-span") as span:
        span.set_attribute("custom.attribute", "test-value")
        span.add_event("test event")
        print("✅ Created first span")

    # Multiple spans to test batching
    for i in range(3):
        with tracer.start_as_current_span(f"batch-span-{i}") as span:
            span.set_attribute("iteration", i)
            span.add_event(f"event-{i}")
            print(f"🟢 Generated span {i}")
        time.sleep(0.1)  # Small delay between spans

    # Allow time for batch processor to flush
    time.sleep(1)
    print("✅ Test complete. All spans processed through fake exporter.")


if __name__ == "__main__":
    test()
