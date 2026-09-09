# Kubernetes deployment

The manifests deploy the stateless API, independent Temporal worker, and React
web workloads. PostgreSQL with pgvector, the independent procurement database,
Redis, MinIO, Temporal, the configured model endpoints, and the observability
services must be reachable by the DNS names in the ConfigMap.

Create the namespace and secret from a protected secret manager, run both
Alembic migrations as one-shot release jobs, set immutable image digests in the
Kustomization, and then apply:

```bash
kubectl apply -f deploy/k8s/namespace.yaml
kubectl apply -f /secure/path/enterprise-agent-secrets.yaml
kubectl apply -k deploy/k8s
```

`secret.example.yaml` is a schema example and is deliberately excluded from
`kustomization.yaml`; never commit live credentials.
