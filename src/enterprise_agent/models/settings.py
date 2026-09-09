"""Local model bootstrap. Production workspace configurations will use encrypted storage."""

import os
from collections.abc import Mapping
from ipaddress import ip_address
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import dotenv_values
from pydantic import SecretStr

from enterprise_agent.models.gateway import ModelConfig


def _load_settings(
    path: Path,
    *,
    environ: Mapping[str, str] | None,
    prefix: str,
    version: str,
) -> tuple[ModelConfig, SecretStr]:
    values = dict(dotenv_values(path))
    values.update(os.environ if environ is None else environ)
    mode_key = f"{prefix}_MODE"
    base_url_key = f"{prefix}_BASE_URL"
    name_key = f"{prefix}_NAME"
    api_key = f"{prefix}_API_KEY"
    mode = str(values.get(mode_key, "external")).strip().lower()
    if mode not in {"local", "external"}:
        raise ValueError(f"{mode_key} must be local or external")
    required = (
        (base_url_key, name_key)
        if mode == "local"
        else (base_url_key, name_key, api_key)
    )
    missing = [
        name for name in required if not values.get(name, "") or not str(values[name]).strip()
    ]
    if missing:
        raise ValueError("Missing model configuration: " + ", ".join(missing))
    url = str(values[base_url_key]).strip().rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in ({"http", "https"} if mode == "local" else {"https"})
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            f"{base_url_key} has an invalid scheme or includes credentials, query or fragment"
        )
    if mode == "local":
        host = parsed.hostname
        try:
            loopback = host in {"localhost", "host.docker.internal"} or ip_address(
                str(host)
            ).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError(
                "Local model mode requires a loopback or Docker host address"
            )
    config = ModelConfig(
        provider="OpenAICompatible",
        base_url=url,
        model=str(values[name_key]).strip(),
        external=mode != "local",
        version=version,
    )
    return config, SecretStr(str(values.get(api_key) or "").strip())


def load_model_settings(
    path: Path = Path(".env"), *, environ: Mapping[str, str] | None = None
) -> tuple[ModelConfig, SecretStr]:
    return _load_settings(
        path,
        environ=environ,
        prefix="MODEL",
        version="local-bootstrap-v1",
    )


def load_embedding_settings(
    path: Path = Path(".env"), *, environ: Mapping[str, str] | None = None
) -> tuple[ModelConfig, SecretStr]:
    return _load_settings(
        path,
        environ=environ,
        prefix="EMBEDDING",
        version="embedding-bootstrap-v1",
    )


def load_reranker_settings(
    path: Path = Path(".env"), *, environ: Mapping[str, str] | None = None
) -> tuple[ModelConfig, SecretStr]:
    return _load_settings(
        path,
        environ=environ,
        prefix="RERANKER",
        version="reranker-bootstrap-v1",
    )
