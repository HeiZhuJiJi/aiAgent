# Resume benchmark protocol (2026-09-29)

Measure the local implementation, not manufacture favorable resume statistics.
Freeze cases before observing results; retain all failures and raw samples.
No existing production behavior or test assertions are changed.

## Fixed scope

- Retrieval: 20 fictional enterprise policy topics, 60 labelled queries in exact-code, English paraphrase and Chinese paraphrase forms, plus topical distractors. Compare real PostgreSQL FTS, real BGE-M3/pgvector and production RRF. Report Recall@1/3/5, MRR@10 and category breakdowns.
- Load: identical query mix, two repetitions at concurrency 1, 4 and 8 (120 requests per level). Measure embedding plus database retrieval p50/p95 and throughput. Warmup is separate. This is a component measurement, not Agent latency.
- Agent: 12 fixed RAG questions using only fictional fixture text and the already configured real model. Measure routing plus task execution plus result retrieval, expected-fact matching, persisted evidence and usage. Completion alone never implies a correct answer.
- Recovery: five real kills of benchmark-owned Workers on unique queues; 20 response-loss trials against the real procurement database. Measure kill dispatch to durable completion and verify authoritative command/external-write counts.

## Controls

Same corpus and queries across baselines; fixed random order; new dedicated workspaces; no fake embeddings or model responses. Do not restart shared services or kill the application Worker. Record code/data hashes and environment metadata without secrets. Preserve JSON/JSONL samples under work/verification/resume-benchmark/. Report synthetic-corpus limits and modest sample sizes. Do not report improvements unsupported by a controlled comparison. Zero observed failures is not a production reliability guarantee. Existing release blockers remain open.

Any setup failure or protocol revision must be documented.
