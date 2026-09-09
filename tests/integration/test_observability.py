import os

import httpx

from apps.api.main import create_app


async def test_http_trace_and_prometheus_endpoint_expose_required_metrics():
    app = create_app(os.environ["TEST_DATABASE_URL"])
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
        ) as client,
    ):
        health = await client.get("/health")
        assert health.status_code == 200
        trace_id = health.headers["x-trace-id"]
        assert len(trace_id) == 32
        int(trace_id, 16)

        response = await client.get("/metrics")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        body = response.text
        for metric in (
            "task_total",
            "task_success_rate",
            "task_duration_seconds",
            "step_duration_seconds",
            "replan_total",
            "llm_call_total",
            "llm_latency_seconds",
            "llm_tokens_total",
            "capability_call_total",
            "capability_failure_total",
            "approval_total",
            "policy_denied_total",
            "recovery_total",
            "unknown_operation_total",
            "reconciliation_total",
            "stale_attempt_rejected_total",
            "http_request_total",
        ):
            assert metric in body
        assert "authorization" not in body.casefold()
        assert "bearer" not in body.casefold()
