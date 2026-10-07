import pytest

from apps.worker.main import resolve_temporal_task_queues


def test_primary_queue_is_first_and_legacy_queues_are_deduplicated() -> None:
    assert resolve_temporal_task_queues(
        "enterprise-agent-runtime",
        "enterprise-agent-runtime-v1, enterprise-agent-runtime",
    ) == ("enterprise-agent-runtime", "enterprise-agent-runtime-v1")


def test_empty_task_queue_configuration_is_rejected() -> None:
    with pytest.raises(ValueError, match="at least one Temporal task queue"):
        resolve_temporal_task_queues(" ", " , ")
