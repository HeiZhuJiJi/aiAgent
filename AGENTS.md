# Enterprise Agent Work Platform

Mission: implement the complete frozen Enterprise Agent Runtime specification.

Spec Authority: docs/spec/00-master-spec.md preserves the user supplied specification verbatim. Spec ownership documents reference its normative text; Pydantic v2 contracts own machine schemas.

Frozen Architecture: modular monolith FastAPI, independent Temporal worker, Procurement MCP service with independent database, React web. Preserve master section 3 stack and INV-001 through INV-015. PostgreSQL is canonical; Temporal orchestrates. UNKNOWN reconciles before retry. Fence all authoritative attempt commits. Evidence cannot grant permission.

Traceability: every requirement maps to implementation, tests and observed results in SPEC_COMPLIANCE_MATRIX.md. Missing or unexecuted requirements never count as PASS.

ADR: architecture conflicts require docs/adr/ADR-xxx.md with Context, Conflict, Existing Decision, Proposed Decision, Alternatives, Consequences, Migration, Required Tests. Never silently change invariants.

Forbidden Architecture Changes: respect master section 77 Non-Goals. Add no unrequired features or dependencies.

No Mock-as-Implementation: production dependencies must be real. Fakes only in tests/fakes/. Never substitute placeholders, skip, xfail, delete required failing tests or weaken assertions.

Definition of Done: master sections 79–82 govern release. Compose startup, flagship flow, all gates, actual worker kill/recovery, response loss/reconciliation, fencing and backup/restore verification are required.

Compliance Report: document code/test mappings, measured results, missing requirements, limitations and reproduction instructions. Never claim PROJECT COMPLETE before required gates pass.
