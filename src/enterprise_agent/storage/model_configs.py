from ipaddress import ip_address
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.contracts import ModelConfigCreate, ModelConfigView
from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.secrets import SecretCipher
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.models.gateway import ModelConfig
from enterprise_agent.storage.tables import AuditRow, ModelConfigRow, SecretRow


def _safe_base_url(value: str, *, external: bool) -> str:
    url = value.strip().rstrip("/")
    parsed = urlsplit(url)
    allowed = {"https"} if external else {"http", "https"}
    if (
        parsed.scheme not in allowed
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise DomainError(FailureCode.VALIDATION_FAILED, "model base URL is invalid")
    if not external and parsed.scheme == "http":
        host = parsed.hostname
        try:
            local = host in {"localhost", "host.docker.internal"} or ip_address(
                str(host)
            ).is_loopback
        except ValueError:
            local = False
        if not local:
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "plain HTTP local models require a loopback or Docker host address",
            )
    return url


def _view(row: ModelConfigRow) -> ModelConfigView:
    return ModelConfigView(
        config_id=row.id,
        workspace_id=row.workspace_id,
        kind=cast(Literal["CHAT", "EMBEDDING", "RERANKER"], row.kind),
        provider=cast(Literal["Qwen", "DeepSeek", "OpenAICompatible"], row.provider),
        base_url=row.base_url,
        model_name=row.model_name,
        external=row.external,
        has_secret=row.secret_id is not None,
        enabled=row.enabled,
        version=row.version,
        created_at=row.created_at,
    )


async def create_model_config(
    session: AsyncSession,
    *,
    identity: Identity,
    value: ModelConfigCreate,
    cipher: SecretCipher,
    trace_id: str,
) -> ModelConfigView:
    base_url = _safe_base_url(value.base_url, external=value.external)
    model_name = value.model_name.strip()
    api_key = value.api_key.get_secret_value().strip()
    if value.external and not api_key:
        raise DomainError(
            FailureCode.VALIDATION_FAILED, "external model API key is required"
        )
    existing = await session.scalar(
        select(ModelConfigRow)
        .where(
            ModelConfigRow.workspace_id == value.workspace_id,
            ModelConfigRow.id == value.config_id,
        )
        .with_for_update()
    )
    secret_id = uuid5(NAMESPACE_URL, f"model-secret:{value.workspace_id}:{value.config_id}")
    if existing is not None:
        same = (
            existing.kind == value.kind
            and existing.provider == value.provider
            and existing.base_url == base_url
            and existing.model_name == model_name
            and existing.external == value.external
            and bool(existing.secret_id) == bool(api_key)
        )
        if same and api_key and existing.secret_id is not None:
            secret = await session.scalar(
                select(SecretRow).where(
                    SecretRow.workspace_id == value.workspace_id,
                    SecretRow.id == existing.secret_id,
                )
            )
            same = secret is not None and cipher.decrypt(
                value.workspace_id, existing.secret_id, secret.ciphertext
            ) == api_key
        if not same:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "model configuration identity already has different content",
            )
        return _view(existing)
    if api_key:
        session.add(
            SecretRow(
                id=secret_id,
                workspace_id=value.workspace_id,
                purpose="MODEL_API_KEY",
                ciphertext=cipher.encrypt(value.workspace_id, secret_id, api_key),
            )
        )
        # There is no ORM relationship between registry records; flush the
        # encrypted envelope before inserting its composite foreign-key owner.
        await session.flush()
    row = ModelConfigRow(
        id=value.config_id,
        workspace_id=value.workspace_id,
        kind=value.kind,
        provider=value.provider,
        base_url=base_url,
        model_name=model_name,
        external=value.external,
        secret_id=secret_id if api_key else None,
    )
    session.add(row)
    session.add(
        AuditRow(
            workspace_id=value.workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="model.configure",
            resource_type="MODEL_CONFIG",
            resource_id=value.config_id,
            result={
                "kind": value.kind,
                "provider": value.provider,
                "model_name": model_name,
                "external": value.external,
                "has_secret": bool(api_key),
            },
            trace_id=trace_id,
        )
    )
    await session.flush()
    return _view(row)


async def list_model_configs(
    session: AsyncSession, workspace_id: UUID
) -> tuple[ModelConfigView, ...]:
    rows = (
        await session.scalars(
            select(ModelConfigRow)
            .where(ModelConfigRow.workspace_id == workspace_id)
            .order_by(ModelConfigRow.created_at, ModelConfigRow.id)
        )
    ).all()
    return tuple(_view(row) for row in rows)


async def load_model_gateway_config(
    session: AsyncSession,
    workspace_id: UUID,
    config_id: UUID,
    cipher: SecretCipher,
) -> tuple[ModelConfig, SecretStr, ModelConfigRow]:
    row = await session.scalar(
        select(ModelConfigRow).where(
            ModelConfigRow.workspace_id == workspace_id,
            ModelConfigRow.id == config_id,
            ModelConfigRow.enabled.is_(True),
        )
    )
    if row is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "model configuration not found")
    key = ""
    if row.secret_id is not None:
        secret = await session.scalar(
            select(SecretRow).where(
                SecretRow.workspace_id == workspace_id,
                SecretRow.id == row.secret_id,
            )
        )
        if secret is None:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION, "model secret reference is unavailable"
            )
        key = cipher.decrypt(workspace_id, row.secret_id, secret.ciphertext)
    return (
        ModelConfig(
            provider=cast(
                Literal["Qwen", "DeepSeek", "OpenAICompatible"], row.provider
            ),
            base_url=row.base_url,
            model=row.model_name,
            external=row.external,
            version=f"workspace-config-{row.version}",
        ),
        SecretStr(key),
        row,
    )
