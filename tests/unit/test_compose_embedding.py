from pathlib import Path

import yaml

COMPOSE_PATH = Path("deploy/compose/infrastructure.yml")
TEI_IMAGE = (
    "ghcr.io/huggingface/text-embeddings-inference:cpu-1.9.3"
    "@sha256:ad950d30878eceb72aaf32024d26fa2b1d04a75304fa0b4776b49aa1941fea07"
)
BGE_M3_REVISION = "5617a9f61b028005a4858fdac845db406aefb181"


def test_compose_manages_a_pinned_embedding_service() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]
    embedding = services["embedding"]

    assert embedding["image"] == TEI_IMAGE
    assert embedding["command"][:4] == [
        "--model-id",
        "${EMBEDDING_SERVICE_MODEL_ID:-BAAI/bge-m3}",
        "--revision",
        f"${{EMBEDDING_SERVICE_MODEL_REVISION:-{BGE_M3_REVISION}}}",
    ]
    assert embedding["ports"] == ["127.0.0.1:8001:80"]
    assert embedding["volumes"] == ["embedding_cache:/data"]
    assert embedding["healthcheck"]["start_period"] == (
        "${EMBEDDING_HEALTH_START_PERIOD:-15m}"
    )
    assert compose["volumes"]["embedding_cache"] is None


def test_runtime_defaults_to_managed_embedding_with_bounded_worker_batches() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]

    assert services["api"]["environment"]["EMBEDDING_BASE_URL"] == (
        "${EMBEDDING_CONTAINER_BASE_URL:-http://embedding:80/v1}"
    )
    assert services["worker"]["environment"]["KNOWLEDGE_EMBED_BATCH_SIZE"] == (
        "${KNOWLEDGE_EMBED_BATCH_SIZE:-8}"
    )
    assert services["worker"]["environment"]["KNOWLEDGE_RERANK_TIMEOUT_SEC"] == (
        "${KNOWLEDGE_RERANK_TIMEOUT_SEC:-3}"
    )
    assert services["api"]["depends_on"]["embedding"]["condition"] == "service_healthy"
    assert services["worker"]["depends_on"]["embedding"]["condition"] == "service_healthy"


def test_runtime_uses_one_stable_temporal_task_queue() -> None:
    compose = yaml.safe_load(COMPOSE_PATH.read_text(encoding="utf-8"))
    services = compose["services"]

    queue = services["api"]["environment"]["TEMPORAL_TASK_QUEUE"]
    assert queue == "${TEMPORAL_TASK_QUEUE:-enterprise-agent-runtime}"
    assert services["worker"]["environment"]["TEMPORAL_TASK_QUEUE"] == queue
    assert services["worker"]["environment"]["TEMPORAL_LEGACY_TASK_QUEUES"] == (
        "${TEMPORAL_LEGACY_TASK_QUEUES:-enterprise-agent-runtime-v1}"
    )
