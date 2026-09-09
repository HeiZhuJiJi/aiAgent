from apps.api.main import app


def test_openapi_contains_identity_workspace_and_uniform_error_contracts():
    schema = app.openapi()
    assert schema["openapi"].startswith("3.")
    assert {
        "/auth/register",
        "/auth/login",
        "/auth/me",
        "/auth/logout",
        "/workspaces",
        "/workspaces/{workspace_id}",
        "/tasks",
        "/workspaces/{workspace_id}/tasks",
        "/workspaces/{workspace_id}/tasks/{task_id}",
        "/workspaces/{workspace_id}/tasks/{task_id}/pause",
        "/workspaces/{workspace_id}/tasks/{task_id}/resume",
        "/workspaces/{workspace_id}/tasks/{task_id}/cancel",
        "/approvals",
        "/approvals/{approval_id}/approve",
        "/approvals/{approval_id}/reject",
    } <= set(schema["paths"])
    components = schema["components"]["schemas"]
    assert set(components["ErrorEnvelope"]["properties"]) == {"error"}
    assert set(components["ErrorDetail"]["properties"]) == {
        "code",
        "message",
        "trace_id",
    }
    assert "password" not in components["UserView"]["properties"]
    assert "token_hash" not in components["SessionView"]["properties"]
