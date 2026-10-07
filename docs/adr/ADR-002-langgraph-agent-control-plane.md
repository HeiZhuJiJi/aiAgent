# ADR-002: LangGraph agent control plane with Temporal durable execution

Status: Accepted

## Context

The current chat and bounded-research paths split understanding and orchestration across
frontend regular expressions, backend regular expressions, a model-assisted route helper,
declarative Skill DAGs and a separate Replan component. Failures are therefore handled by
different clocks and different state owners. Production observations include unrecoverable
model-schema failures, searches that return Evidence but still end in a generic refusal,
attachment turns that lose their document binding, and model calls that continue after the
user-facing deadline.

The required behavior is one governed agent control plane. A Turn must resolve context,
understand intent, choose an execution level and information sources, invoke allowed tools,
validate Evidence, generate an answer and take a bounded recovery action when a step fails.
Planner, Executor and Verifier are roles of the model selected for that Turn, not hard-coded
model providers.

## Conflict

The frozen specification names Temporal as the durable execution infrastructure and lists an
unnecessary LangGraph dependency as a non-goal. Replacing Temporal or PostgreSQL authority with
LangGraph would violate that decision. Keeping the existing custom chat/research orchestration,
however, would preserve duplicated state machines and would not satisfy the explicitly requested
unified agent behavior.

## Existing Decision

PostgreSQL is the canonical business-state store. Temporal orchestrates durable enterprise
tasks, approvals, crash recovery and external side effects. UNKNOWN operations reconcile before
retry, and authoritative attempt commits are fenced. Evidence cannot grant permission.

## Proposed Decision

Use LangGraph StateGraph as the agent reasoning control plane for INLINE_CHAT and BOUNDED_QA,
and as the planning front end for DURABLE_TASK. The graph uses typed state, explicit nodes,
conditional edges, bounded recovery transitions and checkpoint references. It replaces the
custom intent-routing, workflow-selection and semantic-replan orchestration rather than wrapping
that orchestration in a single node.

Temporal remains the sole durable executor for approvals, long-running work and external side
effects. PostgreSQL remains authoritative for Turn, Task, Evidence, Claim, audit and final
outcome. LangGraph checkpoints contain orchestration state and stable identifiers, not an
alternative copy of authoritative business records. Existing Web, RAG, Skill, MCP, policy and
model-gateway implementations become graph nodes or governed tools.

The model binding is resolved when a Turn starts. The web-selected model wins; the configured
default is used only when no selection is supplied. Planner, Executor and conditional Verifier
use that same immutable binding. Cross-model failover is not part of the default agent path.

## Alternatives

- Continue extending the custom router, YAML DAG and Replan code. This retains duplicate state
  owners and has already made failure recovery difficult to reason about.
- Replace Temporal completely with LangGraph. This weakens the frozen durable-execution,
  reconciliation, approval and fencing guarantees.
- Put the whole existing workflow inside one LangGraph node. This adds a dependency without
  transferring orchestration authority and is explicitly rejected.
- Use a prebuilt unrestricted ReAct agent. It cannot enforce the required source boundaries,
  budgets, permissions and external-side-effect invariants.

## Consequences

The agent path gains one visible state machine and typed recovery decisions. The architecture
keeps two orchestrators only at a deliberate boundary: LangGraph owns bounded reasoning, while
Temporal owns durable execution. Adapters are required for current capabilities, and the old
router/DAG/Replan paths must remain only during migration and then be deleted after parity gates
pass. Checkpoint and Turn identifiers must be correlated for traceability.

LangGraph becomes a necessary production dependency. It must not introduce LangSmith as a
required hosted service; tracing continues through the platform's existing OpenTelemetry and
audit facilities.

## Migration

1. Add typed AgentState, IntentFrame, ExecutionDecision, ModelBinding and RecoveryDecision
   contracts plus a single Context Projection boundary.
2. Build the StateGraph with planner, policy, dispatch, tool, Evidence gate, generation,
   conditional verification, bounded recovery and finalization nodes.
3. Migrate direct chat first, preserving the existing API while making the graph authoritative.
4. Migrate Web, selected-document RAG, attachment question answering and document summary into
   BOUNDED_QA tools and subgraphs.
5. Connect DURABLE_TASK decisions to the existing Temporal task start and signal APIs.
6. Adapt installed Skills and MCP capabilities to the governed tool catalog.
7. Remove frontend execution routing, legacy backend routing, research Skill YAML orchestration
   and the old semantic Replan component after regression and recovery tests pass.

## Required Tests

- The graph uses the web-selected model or configured default and keeps one binding for all model
  roles in the Turn.
- Ordinary chat reaches a terminal answer without invoking research or the Verifier.
- Current-information questions cannot use parametric knowledge as Evidence.
- Web, RAG, document summary and mixed-source requests select only allowed tools and source scopes.
- A correction resolves against completed eligible Turns and cannot consume a failed Turn.
- Empty, stale or mismatched Evidence causes one bounded retrieve-more or replan transition.
- Schema failure can repair once without switching model providers or exhausting the total
  deadline.
- Every Turn terminates as ANSWERED, PARTIALLY_ANSWERED, CLARIFICATION_REQUIRED, UNAVAILABLE or
  FAILED; no graph path remains indefinitely RUNNING.
- A timed-out model or tool call observes the Turn deadline, and a late result cannot commit to a
  superseded attempt.
- LangGraph never executes an external side effect directly; durable operations go through
  Temporal policy, approval, idempotency, fencing and reconciliation.
- Worker-kill, response-loss, UNKNOWN reconciliation and duplicate-delivery tests continue to
  pass unchanged.
