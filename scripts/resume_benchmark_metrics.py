"""Explicit descriptive metrics; unsuccessful requests remain in the denominator."""

import math
import re
import statistics


def percentile(values, probability):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(probability * len(ordered)) - 1)]


def latency_summary(values):
    return {
        "n": len(values),
        "mean_ms": statistics.mean(values) if values else None,
        "p50_ms": percentile(values, 0.5),
        "p95_ms": percentile(values, 0.95),
        "max_ms": max(values) if values else None,
    }


def retrieval_metrics(rankings, gold):
    if len(rankings) != len(gold) or not gold:
        raise ValueError("rankings and nonempty gold must have equal length")
    ranks = [
        items.index(target) + 1 if target in items else None
        for items, target in zip(rankings, gold, strict=True)
    ]
    return {
        "n": len(gold),
        **{
            f"recall_at_{k}": sum(r is not None and r <= k for r in ranks) / len(ranks)
            for k in (1, 3, 5)
        },
        "mrr_at_10": sum(1 / r if r is not None and r <= 10 else 0 for r in ranks) / len(ranks),
    }


def expected_facts(answer, expected):
    checks = []
    for alternatives in expected:
        found = False
        for term in alternatives.split("/"):
            pattern = (
                r"(?<![0-9])" + re.escape(term) + r"(?![0-9])"
                if term.isdigit()
                else re.escape(term)
            )
            found |= re.search(pattern, answer, re.IGNORECASE) is not None
        checks.append(found)
    return {
        "checks": checks,
        "matched": sum(checks),
        "total": len(checks),
        "all_matched": bool(checks) and all(checks),
    }
