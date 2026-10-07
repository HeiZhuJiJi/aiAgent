"""Audit retained benchmark samples without repeating production requests."""

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from scripts.resume_benchmark_metrics import expected_facts, latency_summary, retrieval_metrics


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_lines(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def summarize(retrieval, recovery, agent):
    data = read_json(retrieval / "dataset.json")
    queries = {row["id"]: row for row in data["queries"]}
    ranked = read_lines(retrieval / "retrieval.jsonl")
    assert len(ranked) == len(queries)
    assert {row["query_id"] for row in ranked} == set(queries)
    assert all(row["gold"] == queries[row["query_id"]]["gold_document"] for row in ranked)
    retrieval_results = {
        method: retrieval_metrics(
            [row["rankings"][method] for row in ranked], [row["gold"] for row in ranked]
        )
        for method in ("lexical", "vector", "hybrid")
    }
    loads = read_lines(retrieval / "load.jsonl")
    saved_loads = read_json(retrieval / "load_summary.json")
    load_results = []
    for level in (1, 4, 8):
        rows = [row for row in loads if row["concurrency"] == level]
        assert len(rows) == 120
        assert len({row["index"] for row in rows}) == 120
        assert Counter(row["query_id"] for row in rows) == Counter(dict.fromkeys(queries, 2))
        load_results.append(
            {
                "concurrency": level,
                "requests": len(rows),
                "successes": sum(row["success"] for row in rows),
                "all_request_latency": latency_summary([row["latency_ms"] for row in rows]),
                "saved_wall_time_summary": next(
                    row for row in saved_loads if row["concurrency"] == level
                ),
            }
        )
    recovery_results = {}
    for scenario, expected_count, timer in [
        ("worker_crash", 5, "recovery_ms"),
        ("response_loss", 20, "reconciliation_ms"),
    ]:
        rows = read_lines(recovery / (scenario + ".jsonl"))
        assert len(rows) == expected_count
        assert len({row["trial"] for row in rows}) == expected_count
        for row in rows:
            if not row["success"]:
                continue
            if scenario == "worker_crash":
                commands = row["authoritative_commands"]
                assert [c["step"] for c in commands] == ["protected-step", "after-recovery"]
                assert len({c["id"] for c in commands}) == 2
                assert row["duplicate_commits"] == 0
            else:
                assert row["external_rows"] == 1 and row["duplicate_writes"] == 0
        recovery_results[scenario] = {
            "trials": len(rows),
            "successes": sum(row["success"] for row in rows),
            "successful_latency": latency_summary([row[timer] for row in rows if row["success"]]),
        }
    cases = read_json(agent / "cases.json")
    answers = read_lines(agent / "answers.jsonl")
    assert len(answers) == len(cases), "Agent run incomplete; do not finalize the report"
    assert [row["case_id"] for row in answers] == [case["id"] for case in cases]
    documents = {row["id"]: row for row in data["documents"]}
    details = []
    for row, case in zip(answers, cases, strict=True):
        facts = expected_facts(row.get("answer", ""), case["expected"])
        correct = row.get("task_state") == "COMPLETED" and facts["all_matched"]
        assert row["success"] == correct
        expected_hash = hashlib.sha256(
            documents[case["gold_document"]]["content"].encode()
        ).hexdigest()
        details.append(
            {
                "case_id": row["case_id"],
                "task_id": row.get("task_id"),
                "state_at_deadline": row.get("task_state"),
                "success": correct,
                "route": row.get("route", {}).get("route"),
                "gold_document_retained": any(
                    e.get("content_hash") == expected_hash for e in row.get("evidence", [])
                ),
                "end_to_end_ms": row["end_to_end_ms"],
                "task_tokens": row.get("task_tokens", 0),
                "error": row.get("error"),
                "attempt_failures": [
                    a["failure"] for a in row.get("attempts", []) if a.get("failure")
                ],
            }
        )
    agent_results = {
        "planned": len(cases),
        "attempted": len(answers),
        "successful_answers": sum(row["success"] for row in details),
        "states_at_deadline": dict(Counter(row["state_at_deadline"] for row in details)),
        "rag_routes": sum(row["route"] == "RAG" for row in details),
        "gold_documents_retained": sum(row["gold_document_retained"] for row in details),
        "all_attempts_latency": latency_summary([row["end_to_end_ms"] for row in details]),
        "successful_answers_latency": latency_summary(
            [row["end_to_end_ms"] for row in details if row["success"]]
        ),
        "task_tokens_total": sum(row["task_tokens"] for row in details),
        "cases": details,
        "quality_gate": "PASS" if all(row["success"] for row in details) else "FAIL",
    }
    files = {}
    for directory in (retrieval, recovery, agent):
        for path in sorted(directory.iterdir()):
            if path.suffix in {".json", ".jsonl", ".log"}:
                files[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
    return {
        "audited_at": datetime.now(UTC).isoformat(),
        "raw_sample_audit": "PASS",
        "note": "Audit PASS means consistent retained evidence, not Agent quality or release PASS.",
        "retrieval": retrieval_results,
        "load": load_results,
        "recovery": recovery_results,
        "agent": agent_results,
        "evidence_sha256": files,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("retrieval", "recovery", "agent", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.retrieval, args.recovery, args.agent)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                key: value
                for key, value in result.items()
                if key not in {"evidence_sha256", "agent"}
            },
            ensure_ascii=True,
        )
    )
    print(
        json.dumps(
            {key: value for key, value in result["agent"].items() if key != "cases"},
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
