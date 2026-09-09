# Implementation plan

One complete product, ordered by dependencies. The frozen scope is not divided into product delivery phases.

1. Preserve frozen source and checksum; assign requirement IDs and ownership; establish compliance matrix.
2. Verify Python 3.12, Node and real infrastructure; establish packaging, locks, lint/type/test tools and deployment.
3. Implement versioned Pydantic contracts, migrations, state machines, immutable snapshots/plans, workspace isolation, fencing and domain command idempotency.
4. Implement identity, RBAC, encrypted secrets, deterministic policy/egress, bound approvals, audit and outbox.
5. Implement independent procurement database and official MCP service, UNKNOWN reconciliation/compensation, MCP management and declarative Skill quarantine/install lifecycle.
6. Implement governed model gateway, structured repair, budgets/context, parsing, BGE hybrid retrieval/reranking, evidence/claims, review and artifacts.
7. Integrate generic planner/DAG scheduler/replan, Temporal activities/signals/heartbeat/continuation, backpressure, recovery and cancellation.
8. Implement API and React application covering chat, tasks, knowledge, skills, MCP, approvals, artifacts, traces, audit and configuration.
9. Implement observability stack, retention/deletion, backup/restore, Kubernetes and CI.
10. Generate three suppliers, business documents and supplier-risk package; curate 60–80 golden tasks and six ablations.
11. Execute lint/type/unit/contract/integration/security/failure/e2e/evaluation gates, actual worker crash and response loss tests; repair without lowering requirements.
12. Verify flagship and backup/restore, complete traceability, README and FINAL_COMPLIANCE_REPORT.md. Declare complete only after all acceptance gates pass.

Store actual verification commands, environment, exit status and logs in work/verification. Never label unrun checks PASS.
