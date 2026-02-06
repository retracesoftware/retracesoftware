import time

from opencensus.trace.base_exporter import Exporter
from opencensus.trace.samplers import AlwaysOnSampler
from opencensus.trace.tracer import Tracer


class PrintSpanExporter(Exporter):
    def export(self, span_datas):
        print(f"[EXPORT] Exporting {len(span_datas)} spans", flush=True)
        for span in span_datas:
            try:
                span_name = getattr(span, "name", "unknown")
                start_time = getattr(span, "start_time", None)
                end_time = getattr(span, "end_time", None)

                if start_time and end_time:
                    duration = (end_time - start_time).total_seconds()
                    print(f"[SPAN] name={span_name}, duration={duration:.3f}s", flush=True)
                else:
                    print(f"[SPAN] name={span_name}, timing info unavailable", flush=True)
            except Exception as e:
                print(f"[SPAN] Error processing span: {e}", flush=True)


def simulated_work():
    print("Running simulated work...", flush=True)
    time.sleep(0.1)


def main():
    tracer = Tracer(exporter=PrintSpanExporter(), sampler=AlwaysOnSampler())

    with tracer.span(name="root-span"):
        print("Started root span", flush=True)
        simulated_work()

        with tracer.span(name="child-span"):
            print("Started child span", flush=True)
            simulated_work()
            print("Finished child span", flush=True)

        print("Finished root span", flush=True)

    print("OpenCensus tracing test completed successfully!", flush=True)


if __name__ == "__main__":
    print("=== opencensus_test ===", flush=True)
    main()
