import os

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

_provider = TracerProvider(
    resource=Resource.create(
        {
            "service.name": os.environ.get(
                "OTEL_SERVICE_NAME", "enterprise-agent-runtime"
            ),
            "service.version": "0.1.0",
        }
    )
)
_trace_endpoint = os.environ.get("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "").strip()
if _trace_endpoint:
    _provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=_trace_endpoint))
    )
trace.set_tracer_provider(_provider)

tracer = trace.get_tracer("enterprise-agent-runtime", "0.1.0")


def current_trace_id() -> str:
    context = trace.get_current_span().get_span_context()
    if context.is_valid:
        return f"{context.trace_id:032x}"
    return ""
