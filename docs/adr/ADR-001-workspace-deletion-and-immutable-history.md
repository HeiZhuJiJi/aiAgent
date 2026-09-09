# ADR-001: Workspace deletion and immutable history

Status: Proposed

## Context

The frozen specification requires append-only audit, plans, evidence and other
historical records. Section 63 also requires workspace deletion to remove
business and vector data, MinIO objects and secrets, with an optional minimal
audit tombstone.

## Conflict

Database triggers correctly reject ordinary deletion of immutable history.
Those same triggers prevent the mandated lifecycle purge. Retaining the full
task graph solely for audit foreign keys would retain business data.

## Existing Decision

Immutable rows cannot be changed or deleted by normal runtime transactions.
Audit is append-only and PostgreSQL is canonical.

## Proposed Decision

Use a narrow lifecycle purge context that accepts DELETE only when the row
workspace matches a transaction-local deletion request. The lifecycle worker
may set it only after the workspace is disabled and every task is terminal.
Detailed audit would be removed and one minimal `workspace.deleted` tombstone
would be appended when configured.

## Alternatives

- Soft deletion leaves business data.
- Keeping complete audit retains the task graph.
- Globally disabling triggers weakens all transactions.
- A separate archival database adds infrastructure outside the frozen scope.

## Consequences

Normal writes retain immutable trigger protection. The purge path would be
explicit, workspace scoped, ordered and idempotent. It requires a privileged
database operation and therefore explicit approval before implementation.

## Migration

First add lifecycle state, retention policies and durable deletion requests.
After approval, add the scoped database purge operation and historical
tombstone.

## Required Tests

- Ordinary mutation and deletion of immutable rows still fail.
- A workspace is disabled before cancellation is requested.
- Purge refuses while a task is active.
- Vector rows, business rows, secrets and workspace-prefixed MinIO objects are
  removed after drain.
- Only the minimal audit tombstone remains when configured.
- Cross-workspace data is unaffected.
