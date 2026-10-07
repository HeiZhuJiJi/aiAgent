# Release Evidence Package

Prepared: 2026-09-10  
Candidate tag: `v1.0-enterprise-agent-runtime`  
Recommendation: **HOLD — evidence package is ready, repository-wide Definition of Done is not**

## Human-readable reports

| Artifact | Purpose | SHA-256 |
|---|---|---|
| `PRODUCTION_AGENT_TASK_REPORT.md` | Database-backed production Agent Task, Plan, Steps, calls, Usage, Trace, Evidence and Claims | `f59022a4b51407a3ccae34a6dc3c1293883ebfc3925a08bd622d351b7460232a` |
| `FAILURE_RECOVERY_CASE_REPORT.md` | Worker crash, `UNKNOWN → Reconcile`, Admission Control/basic Workspace fairness | `1f2d240f51db7558433c5952b28e38c117812355552d54bd184641a4e972c400` |

## Machine-readable and test evidence

| Artifact | Result | SHA-256 |
|---|---|---|
| `work/verification/pytest.xml` | 217 passed, 0 failed/skipped/xfailed | `2d2afa032677bbd38cebaa575e624ce8ca1cd19df7eb53a211a61315af16a99a` |
| `work/verification/evaluation/report.json` | 72/72 bindings; record-backed quality metrics | `e06161cefd75842c6e0c889a7644699ae4cb10a4ac261a39ff14088dcbad4d7c` |
| `work/verification/evaluation/junit.xml` | 63/63 deduplicated Evaluation nodes | `b0ff64922869e151e650d3d66b261aeaa769a0d749ff1492da17c8f5488fdc1e` |
| `work/verification/admission-control-junit.xml` | 3/3 Admission Control checks | `577e03b52f617fa9b02a180d9fe66a39fffdd980d63f5e8cf98bd5d0874f5fa2` |
| `work/verification/model-live.json` | Real production-provider structured probe | `7fb371548b4c07a56dda66bc7d93302a30ab6d45d1c122c84e320a6260fd055e` |
| `work/verification/evaluation/ablation-report.json` | Six controlled component ablations | `17e1f86f8265a3029c456efab0e24d95d3de437896c89f3c717d7cfd78866096` |

## Release status

The synchronized Requirement Matrix contains 116 bundles: 53 PASS, 38 PARTIAL and 25 NOT_IMPLEMENTED. The Production Provider Gate and requested Evaluation quality closure pass. The v1.0 tag remains on hold because the frozen Definition of Done still includes open Evaluation dataset dimensions, stale failure cleanup, cancellation compensation, production least-privilege deployment, production BGE/Reranker/Web validation and irreversible lifecycle work that remains behind ADR-001.

No secret or API key is included. `.env` remains ignored. No failure history was deleted or rewritten.
