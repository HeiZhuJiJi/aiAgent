import pytest

from scripts.resume_benchmark_data import dataset
from scripts.resume_benchmark_metrics import expected_facts, percentile, retrieval_metrics


def test_fixture_labels_are_complete_and_unique():
    data = dataset()
    ids = [item["id"] for item in data["documents"]]
    assert len(ids) == len(set(ids)) == 100
    assert len(data["queries"]) == 60
    assert all(item["gold_document"] in ids for item in data["queries"])
    assert {item["category"] for item in data["queries"]} == {"code", "english", "chinese"}


def test_missing_results_count_against_recall_and_mrr():
    result = retrieval_metrics([["a"], [], ["b", "c", "a"]], ["a", "b", "a"])
    assert result["recall_at_1"] == pytest.approx(1 / 3)
    assert result["recall_at_3"] == pytest.approx(2 / 3)
    assert result["mrr_at_10"] == pytest.approx((1 + 1 / 3) / 3)


def test_quantiles_use_nearest_rank_and_do_not_hide_empty_samples():
    assert percentile(list(range(1, 21)), 0.95) == 19
    assert percentile([], 0.95) is None


def test_numeric_fact_checks_reject_accidental_substrings():
    assert not expected_facts("费用是1620元", ["620"])["all_matched"]
    assert expected_facts("每晚620元，由采购部门审批", ["620", "采购/procurement"])["all_matched"]
    assert not expected_facts("", [])["all_matched"]
