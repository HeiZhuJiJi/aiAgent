from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

REGISTRY = CollectorRegistry(auto_describe=True)

HTTP_REQUEST_TOTAL = Counter(
    "http_request_total",
    "HTTP requests handled by the platform API.",
    ("method", "route", "status"),
    registry=REGISTRY,
)
HTTP_REQUEST_DURATION = Histogram(
    "http_request_duration_seconds",
    "Platform API request duration.",
    ("method", "route"),
    registry=REGISTRY,
)
TASK_TOTAL = Counter("task_total", "Tasks created.", registry=REGISTRY)
TASK_SUCCESS_RATE = Gauge("task_success_rate", "Observed task success ratio.", registry=REGISTRY)
TASK_DURATION = Histogram(
    "task_duration_seconds", "Task execution duration.", registry=REGISTRY
)
STEP_DURATION = Histogram(
    "step_duration_seconds", "Task step execution duration.", registry=REGISTRY
)
REPLAN_TOTAL = Counter("replan_total", "Agent replans.", registry=REGISTRY)
LLM_CALL_TOTAL = Counter("llm_call_total", "LLM calls.", ("provider",), registry=REGISTRY)
LLM_LATENCY = Histogram(
    "llm_latency_seconds", "LLM request latency.", ("provider",), registry=REGISTRY
)
LLM_TOKENS = Counter(
    "llm_tokens_total", "LLM tokens consumed.", ("provider", "direction"), registry=REGISTRY
)
CAPABILITY_CALL_TOTAL = Counter(
    "capability_call_total", "Capability provider calls.", ("capability",), registry=REGISTRY
)
CAPABILITY_FAILURE_TOTAL = Counter(
    "capability_failure_total",
    "Capability failures.",
    ("capability", "outcome"),
    registry=REGISTRY,
)
APPROVAL_TOTAL = Counter(
    "approval_total", "Approval decisions.", ("decision",), registry=REGISTRY
)
POLICY_DENIED_TOTAL = Counter(
    "policy_denied_total", "Policy denials.", ("resource",), registry=REGISTRY
)
RECOVERY_TOTAL = Counter("recovery_total", "Durable recoveries.", registry=REGISTRY)
UNKNOWN_OPERATION_TOTAL = Counter(
    "unknown_operation_total", "Operations entering UNKNOWN.", registry=REGISTRY
)
RECONCILIATION_TOTAL = Counter(
    "reconciliation_total", "Operation reconciliations.", ("conclusion",), registry=REGISTRY
)
STALE_ATTEMPT_REJECTED_TOTAL = Counter(
    "stale_attempt_rejected_total", "Rejected stale attempts.", registry=REGISTRY
)


def render_metrics() -> bytes:
    return generate_latest(REGISTRY)


def start_worker_metrics_server(port: int = 9464) -> None:
    start_http_server(port, registry=REGISTRY)
