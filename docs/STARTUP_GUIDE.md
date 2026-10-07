# Local Startup Guide

This guide starts and stops the existing Docker Compose development deployment on Windows. The scripts do not change the deployment architecture, replace providers, create mock services or delete persistent data.

## Prerequisites

- Windows 10 or Windows 11
- Docker Desktop with the Linux container engine running
- Docker Compose v2 (`docker compose`)
- At least 12 GB of memory available to Docker is recommended for the CPU BGE-M3 service
- Internet access and several GB of free disk space are required on the first embedding startup
- Python 3.12 with the project dependencies only when `.env` does not yet exist; an existing `.venv312` is preferred automatically

Run commands from the repository root.

## Start

```powershell
.\start.cmd
```

The script performs these steps in order:

1. Verifies that the Docker CLI and Docker Compose v2 are installed.
2. Verifies that the Docker Engine is reachable.
3. Reuses `.env`; when it is absent, calls the existing `scripts/configure_local.py` helper without printing generated secrets.
4. validates the existing Compose configuration.
5. Builds and starts the complete Compose project.
6. Waits up to 1,800 seconds by default for service health and one-shot
   migration/namespace jobs. Set the process environment variable
   `STARTUP_WAIT_TIMEOUT_SECONDS` before running the script to override it.
7. Prints all Compose service states and returns a non-zero exit code if startup or health checks fail.

The existing Compose dependency graph starts:

- PostgreSQL with pgvector
- Independent Procurement PostgreSQL
- Redis
- MinIO
- Temporal schema, server and default namespace
- Platform and Procurement migrations
- Hugging Face Text Embeddings Inference (CPU) with BAAI/bge-m3
- API
- independent Temporal Worker
- SearXNG
- the governed Web Search provider adapter
- Web
- OpenTelemetry Collector, Prometheus, Tempo, Loki and Grafana

## Access

| Service | Address |
|---|---|
| Web | <http://localhost:55173> |
| API documentation | <http://localhost:58080/docs> |
| API health | <http://localhost:58080/health> |
| Embedding health | <http://localhost:8001/health> |
| MinIO console | <http://localhost:59001> |
| Prometheus | <http://localhost:59090> |
| Grafana | <http://localhost:53000> by default; configurable with `GRAFANA_HOST_PORT` |

Host ports are bound to `127.0.0.1` by the existing Compose configuration.

## Stop without deleting data

```powershell
.\stop.cmd
```

The stop script gives Compose services a 60-second graceful shutdown window. The Web container uses Compose's init process so termination signals reach Node, while the Worker uses the same init process plus `SIGINT` so Python can cancel its async tasks and leave the Temporal worker context. These are process-lifecycle settings only; they do not change the Runtime architecture. The script does not call `down`, does not remove containers, and never passes `--volumes`. PostgreSQL, Procurement DB, MinIO, Prometheus, Tempo and Loki named volumes remain available for the next start.

After stopping, the script inspects every container exit code. If any service reports exit code 137, the script returns an error instead of presenting a forced shutdown as successful.

Do not use `docker compose down -v` for normal project shutdown. That command deletes named volumes and is intentionally absent from the supplied scripts.

## Provider configuration

The Compose stack includes a real CPU embedding provider. Chat and reranker
providers remain external, and provider-dependent calls fail closed while those
providers are unavailable. Configure provider overrides only in ignored local
`.env` values:

- `MODEL_CONTAINER_BASE_URL`, `MODEL_NAME`, `MODEL_API_KEY`
- `EMBEDDING_CONTAINER_BASE_URL`, `EMBEDDING_NAME`, `EMBEDDING_API_KEY`
- `RERANKER_CONTAINER_BASE_URL`, `RERANKER_NAME`, `RERANKER_API_KEY`
- `WEB_CONTAINER_BASE_URL`, `WEB_API_KEY`

From a container, `127.0.0.1` refers to that container, not the Windows host. For a provider running on Windows, use `host.docker.internal` in the corresponding container URL. Never commit `.env`.

By default, leave `EMBEDDING_CONTAINER_BASE_URL` unset or empty. API and Worker
then use the Compose service at `http://embedding:80/v1`; its loopback-only
host endpoint is `http://127.0.0.1:8001/v1`. To use an external
OpenAI-compatible embedding provider instead, set
`EMBEDDING_CONTAINER_BASE_URL` explicitly and set `EMBEDDING_NAME` to the
model name expected by that provider.

The managed service is pinned to the TEI CPU 1.9.3 image digest and the
`BAAI/bge-m3` model revision
`5617a9f61b028005a4858fdac845db406aefb181`. The first start downloads and
warms that model, which can take 10–30 minutes or longer depending on network
and CPU speed. Files are retained in the `embedding_cache` named volume, so
normal stop/start cycles do not download them again. The Worker sends at most
eight texts per embedding request by default; override
`KNOWLEDGE_EMBED_BATCH_SIZE` only after measuring available memory and
latency.

For a slower first start, run:

```powershell
$env:STARTUP_WAIT_TIMEOUT_SECONDS = "3600"
.\start.cmd
```

Changing `.env` does not rewrite model configurations already saved from the
Web UI. Those configurations are versioned Workspace records. Open **模型**,
select the current model, choose **编辑**, replace its Base URL, and save the
new version. The existing encrypted API key is preserved when the key field is
left blank.

An HTTP 401 from `/v1/models` means the endpoint is reachable but rejected the
configured API key. Fix the key or the upstream authentication policy; changing
Docker networking will not resolve a 401.

## Troubleshooting

### Docker is not reachable

Start Docker Desktop and wait until its engine reports running. Verify:

```powershell
docker info
docker compose version
```

### `.env` creation fails

The existing configuration helper requires the project Python dependencies, including `cryptography`. Create the documented virtual environment first, or copy `.env.example` to `.env` and fill every required secret. The helper never prints secret values.

### Port already in use

The existing deployment uses ports `55173`, `58080`, `55432`, `55433`,
`56379`, `59000`, `59001`, `57233`, `59090`, `53000` and
`8001`. Identify the process owning the port before changing it. For a
Grafana-only conflict, set `GRAFANA_HOST_PORT` in `.env` to an available
port (for example `53001`). Before recreating an older container without a
`/var/lib/grafana` data volume, back up and migrate its data. Then run:

```powershell
docker compose --env-file .env up -d --no-deps grafana
```

`start.cmd` displays the actual published Grafana address. Other ports are
unchanged. Grafana stores its data in the `grafana_data` volume. When
upgrading an older container without this data mount, back up and migrate
`/var/lib/grafana` before recreating it; do not discard the old data.

### Temporal namespace initialization fails

`temporal-namespace` is a one-shot initializer: `Exited (0)` is its expected
successful state. Worker startup requires this successful exit. A namespace
that already exists must be reused; it must not be deleted to fix startup.

The initializer now performs bounded checks and registration reconciliation,
and succeeds only after it can describe `default`. CLI errors remain visible,
and an unreachable service still causes a nonzero exit. Inspect and retry with:

```powershell
docker compose --env-file .env logs --tail 100 temporal-namespace
docker compose --env-file .env up -d --wait --wait-timeout 120
```

### A service is unhealthy or startup times out

`start.cmd` prints the complete service table and returns the Compose failure code. Inspect the affected service without hiding earlier failures:

```powershell
docker compose --env-file .env ps --all
docker compose --env-file .env logs --tail 200 SERVICE
```

Useful service names include `postgres`, `procurement-db`, `temporal`,
`platform-migrate`, `procurement-migrate`, `embedding`, `searxng`,
`web-provider`, `api`, `worker` and `web`.

### Embedding startup is slow or unhealthy

On the first start, inspect the model download and warmup directly:

```powershell
docker compose --env-file .env logs --tail 200 embedding
```

Do not retry knowledge ingestion until `embedding` is healthy. A normal
`stop.cmd` preserves the cached model. If an external provider is intended,
verify `EMBEDDING_CONTAINER_BASE_URL` from the Worker container and confirm
that it implements the OpenAI-compatible `/v1/embeddings` endpoint with
1,024-dimensional vectors.

### API or Worker cannot reach a host provider

Confirm that the provider is listening on an address reachable outside its own process and that the `*_CONTAINER_BASE_URL` uses `host.docker.internal` or another container-reachable HTTPS host. Test from the actual caller container rather than relying only on a Windows-host request.

### A migration job fails

The API and Worker depend on successful platform and Procurement migrations. Read the failed job logs; do not bypass the job or mark it successful manually:

```powershell
docker compose --env-file .env logs --tail 200 platform-migrate
docker compose --env-file .env logs --tail 200 procurement-migrate
```

## Data safety

- `start.cmd` may build or recreate containers through normal Compose behavior; canonical data remains in named volumes.
- `stop.cmd` stops services only.
- Neither script performs purge, database reset, volume deletion or immutable-record cleanup.
- Workspace deletion and retention purge remain behind the existing ADR-001 safety boundary.
