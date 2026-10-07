import hashlib
import json
from ipaddress import ip_address
from typing import Literal, cast
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.api.contracts import (
    ModelConfigCreate,
    ModelConfigStateInput,
    ModelConfigUpdate,
    ModelConfigView,
)
from enterprise_agent.contracts.domain import FailureCode, VersionRef
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.secrets import SecretCipher
from enterprise_agent.identity.contracts import Identity
from enterprise_agent.models.gateway import ModelConfig
from enterprise_agent.models.settings import load_model_settings
from enterprise_agent.storage.tables import AuditRow, ModelConfigRow, SecretRow

ModelGatewayConfig = tuple[ModelConfig, SecretStr, ModelConfigRow]
FrozenModelGatewayConfig = tuple[ModelConfig, SecretStr]


def _version_ref(identifier: str, version: str) -> VersionRef:
    digest = hashlib.sha256(f"{identifier}:{version}".encode()).hexdigest()
    return VersionRef(identifier=identifier, version=version, sha256=digest)


def default_model_version_ref(config: ModelConfig) -> VersionRef:
    """Freeze the public bootstrap model configuration without persisting its secret."""

    version = json.dumps(
        {
            "base_url": config.base_url,
            "config_version": config.version,
            "external": config.external,
            "max_output_tokens": config.max_output_tokens,
            "model": config.model,
            "provider": config.provider,
            "timeout_sec": config.timeout_sec,
        },
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return _version_ref("model.default", version)


def _endpoint_identity(value: str) -> tuple[str, str, int | None, str]:
    parsed = urlsplit(value.rstrip("/"))
    port = parsed.port
    if (parsed.scheme.casefold(), port) in {("http", 80), ("https", 443)}:
        port = None
    return (
        parsed.scheme.casefold(),
        (parsed.hostname or "").casefold(),
        port,
        parsed.path.rstrip("/"),
    )


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
            local = (
                host in {"localhost", "host.docker.internal"} or ip_address(str(host)).is_loopback
            )
        except ValueError:
            local = False
        if not local:
            raise DomainError(
                FailureCode.VALIDATION_FAILED,
                "plain HTTP local models require a loopback or Docker host address",
            )
    return url


def _view(row: ModelConfigRow, *, superseded: bool = False) -> ModelConfigView:
    return ModelConfigView(
        config_id=row.id,
        workspace_id=row.workspace_id,
        kind=cast(Literal["CHAT", "EMBEDDING", "RERANKER"], row.kind),
        provider=cast(Literal["Qwen", "DeepSeek", "OpenAICompatible"], row.provider),
        base_url=row.base_url,
        model_name=row.model_name,
        external=row.external,
        has_secret=row.secret_id is not None,
        enabled=row.enabled and not superseded,
        superseded=superseded,
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
        raise DomainError(FailureCode.VALIDATION_FAILED, "external model API key is required")
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
            same = (
                secret is not None
                and cipher.decrypt(value.workspace_id, existing.secret_id, secret.ciphertext)
                == api_key
            )
        if not same:
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "model configuration identity already has different content",
            )
        return _view(existing)
    superseded_ids = select(ModelConfigRow.supersedes_config_id).where(
        ModelConfigRow.workspace_id == value.workspace_id,
        ModelConfigRow.supersedes_config_id.is_not(None),
    )
    duplicate = await session.scalar(
        select(ModelConfigRow.id).where(
            ModelConfigRow.workspace_id == value.workspace_id,
            ModelConfigRow.kind == value.kind,
            ModelConfigRow.model_name == model_name,
            ModelConfigRow.base_url == base_url,
            ModelConfigRow.enabled.is_(True),
            ModelConfigRow.id.not_in(superseded_ids),
        )
    )
    if duplicate is not None:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "model configuration already exists; edit the existing configuration",
        )
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


async def update_model_config(
    session: AsyncSession,
    *,
    identity: Identity,
    config_id: UUID,
    value: ModelConfigUpdate,
    cipher: SecretCipher,
    trace_id: str,
) -> ModelConfigView:
    row = await session.scalar(
        select(ModelConfigRow)
        .where(
            ModelConfigRow.workspace_id == value.workspace_id,
            ModelConfigRow.id == config_id,
        )
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "model configuration not found")
    if row.version != value.expected_version:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "model configuration version mismatch",
        )
    successor = await session.scalar(
        select(ModelConfigRow.id).where(
            ModelConfigRow.workspace_id == value.workspace_id,
            ModelConfigRow.supersedes_config_id == config_id,
        )
    )
    if successor is not None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "model configuration has already been superseded",
        )

    base_url = _safe_base_url(value.base_url, external=value.external)
    model_name = value.model_name.strip()
    api_key = value.api_key.get_secret_value().strip() if value.api_key is not None else ""
    if value.external and not api_key and row.secret_id is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "external model API key is required")
    superseded_ids = select(ModelConfigRow.supersedes_config_id).where(
        ModelConfigRow.workspace_id == value.workspace_id,
        ModelConfigRow.supersedes_config_id.is_not(None),
    )
    duplicate = await session.scalar(
        select(ModelConfigRow.id).where(
            ModelConfigRow.workspace_id == value.workspace_id,
            ModelConfigRow.kind == value.kind,
            ModelConfigRow.model_name == model_name,
            ModelConfigRow.base_url == base_url,
            ModelConfigRow.id != config_id,
            ModelConfigRow.enabled.is_(True),
            ModelConfigRow.id.not_in(superseded_ids),
        )
    )
    if duplicate is not None:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "another model configuration already uses this kind, name, and base URL",
        )

    replacement_id = uuid4()
    secret_id = row.secret_id
    if api_key:
        secret_id = uuid5(NAMESPACE_URL, f"model-secret:{value.workspace_id}:{replacement_id}")
        session.add(
            SecretRow(
                id=secret_id,
                workspace_id=value.workspace_id,
                purpose="MODEL_API_KEY",
                ciphertext=cipher.encrypt(value.workspace_id, secret_id, api_key),
            )
        )
        await session.flush()
    replacement = ModelConfigRow(
        id=replacement_id,
        workspace_id=value.workspace_id,
        kind=value.kind,
        provider=value.provider,
        base_url=base_url,
        model_name=model_name,
        external=value.external,
        secret_id=secret_id,
        enabled=True,
        version=row.version + 1,
        supersedes_config_id=row.id,
    )
    session.add(replacement)
    session.add(
        AuditRow(
            workspace_id=value.workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="model.update",
            resource_type="MODEL_CONFIG",
            resource_id=replacement_id,
            result={
                "kind": value.kind,
                "provider": value.provider,
                "model_name": model_name,
                "external": value.external,
                "secret_rotated": bool(api_key),
                "version": replacement.version,
                "supersedes_config_id": str(row.id),
            },
            trace_id=trace_id,
        )
    )
    await session.flush()
    return _view(replacement)


async def set_model_config_state(
    session: AsyncSession,
    *,
    identity: Identity,
    config_id: UUID,
    value: ModelConfigStateInput,
    trace_id: str,
) -> ModelConfigView:
    row = await session.scalar(
        select(ModelConfigRow)
        .where(
            ModelConfigRow.workspace_id == value.workspace_id,
            ModelConfigRow.id == config_id,
        )
        .with_for_update()
    )
    if row is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "model configuration not found")
    if row.version != value.expected_version:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "model configuration version mismatch",
        )
    successor = await session.scalar(
        select(ModelConfigRow.id).where(
            ModelConfigRow.workspace_id == value.workspace_id,
            ModelConfigRow.supersedes_config_id == config_id,
        )
    )
    if successor is not None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "model configuration has already been superseded",
        )
    if row.enabled == value.enabled:
        return _view(row)
    replacement_id = uuid4()
    replacement = ModelConfigRow(
        id=replacement_id,
        workspace_id=value.workspace_id,
        kind=row.kind,
        provider=row.provider,
        base_url=row.base_url,
        model_name=row.model_name,
        external=row.external,
        secret_id=row.secret_id,
        enabled=value.enabled,
        version=row.version + 1,
        supersedes_config_id=row.id,
    )
    session.add(replacement)
    session.add(
        AuditRow(
            workspace_id=value.workspace_id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="model.enable" if value.enabled else "model.disable",
            resource_type="MODEL_CONFIG",
            resource_id=replacement_id,
            result={
                "enabled": value.enabled,
                "version": replacement.version,
                "supersedes_config_id": str(row.id),
            },
            trace_id=trace_id,
        )
    )
    await session.flush()
    return _view(replacement)


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
    superseded = {row.supersedes_config_id for row in rows if row.supersedes_config_id is not None}
    return tuple(_view(row, superseded=row.id in superseded) for row in rows)


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
    successor = await session.scalar(
        select(ModelConfigRow.id).where(
            ModelConfigRow.workspace_id == workspace_id,
            ModelConfigRow.supersedes_config_id == config_id,
        )
    )
    if successor is not None:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "model configuration has been superseded",
        )
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
            provider=cast(Literal["Qwen", "DeepSeek", "OpenAICompatible"], row.provider),
            base_url=row.base_url,
            model=row.model_name,
            external=row.external,
            version=f"workspace-config-{row.version}",
        ),
        SecretStr(key),
        row,
    )


async def _load_frozen_model_gateway_config(
    session: AsyncSession,
    workspace_id: UUID,
    reference: VersionRef,
    cipher: SecretCipher,
) -> ModelGatewayConfig:
    prefix = "model.config:"
    if not reference.identifier.startswith(prefix):
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "Task model snapshot contains an invalid configuration reference",
        )
    try:
        config_id = UUID(reference.identifier.removeprefix(prefix))
    except ValueError:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "Task model snapshot contains an invalid configuration identifier",
        ) from None
    row = await session.scalar(
        select(ModelConfigRow).where(
            ModelConfigRow.workspace_id == workspace_id,
            ModelConfigRow.id == config_id,
        )
    )
    if row is None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "Task model snapshot references an unavailable configuration revision",
        )
    expected_version = f"{row.version}:{row.provider}:{row.model_name}"
    expected_hash = hashlib.sha256(
        f"{reference.identifier}:{expected_version}".encode()
    ).hexdigest()
    if reference.version != expected_version or reference.sha256 != expected_hash:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "Task model snapshot configuration revision mismatch",
        )
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
                FailureCode.CONSISTENCY_VIOLATION,
                "Task model snapshot secret reference is unavailable",
            )
        key = cipher.decrypt(workspace_id, row.secret_id, secret.ciphertext)
    return (
        ModelConfig(
            provider=cast(Literal["Qwen", "DeepSeek", "OpenAICompatible"], row.provider),
            base_url=row.base_url,
            model=row.model_name,
            external=row.external,
            version=f"workspace-config-{row.version}",
        ),
        SecretStr(key),
        row,
    )


async def load_frozen_chat_model_gateway_chain(
    session: AsyncSession,
    workspace_id: UUID,
    references: tuple[VersionRef, ...],
    cipher: SecretCipher,
) -> tuple[ModelGatewayConfig, ...]:
    """Load the exact model revisions pinned by an immutable Task snapshot.

    Historical revisions remain executable after an administrator edits or
    disables the current model. Interactive Turn creation still uses the
    active-only loader above.
    """

    if len(references) not in {1, 2}:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "Task model snapshot must contain one primary and at most one fallback",
        )
    chain = tuple(
        [
            await _load_frozen_model_gateway_config(
                session, workspace_id, reference, cipher
            )
            for reference in references
        ]
    )
    if any(item[2].kind != "CHAT" for item in chain):
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "Task model snapshot references a non-chat model",
        )
    if len({item[2].id for item in chain}) != len(chain):
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "Task model snapshot repeats a configuration revision",
        )
    if len(chain) == 2:
        primary, fallback = chain
        if (
            _endpoint_identity(primary[0].base_url)
            == _endpoint_identity(fallback[0].base_url)
            and primary[0].model == fallback[0].model
        ):
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "Task fallback snapshot repeats the primary endpoint and model",
            )
    return chain


async def load_frozen_task_model_gateway_chain(
    session: AsyncSession,
    workspace_id: UUID,
    references: tuple[VersionRef, ...],
    cipher: SecretCipher | None,
) -> tuple[FrozenModelGatewayConfig, ...]:
    """Resolve every durable model call from its immutable ExecutionSnapshot."""

    if len(references) == 1 and references[0].identifier == "model.default":
        config, key = load_model_settings()
        if references[0] != default_model_version_ref(config):
            raise DomainError(
                FailureCode.CONSISTENCY_VIOLATION,
                "Task default model differs from its frozen execution snapshot",
            )
        return ((config, key),)
    if not references or any(
        not reference.identifier.startswith("model.config:") for reference in references
    ):
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "Task model snapshot contains an unsupported configuration reference",
        )
    if cipher is None:
        raise DomainError(
            FailureCode.CONSISTENCY_VIOLATION,
            "secret master key is not configured",
        )
    chain = await load_frozen_chat_model_gateway_chain(
        session,
        workspace_id,
        references,
        cipher,
    )
    return tuple((config, key) for config, key, _ in chain)


async def load_chat_model_gateway_chain(
    session: AsyncSession,
    workspace_id: UUID,
    primary_config_id: UUID,
    fallback_config_id: UUID | None,
    cipher: SecretCipher,
) -> tuple[ModelGatewayConfig, ...]:
    """Load one explicit CHAT model and, optionally, one explicit fallback.

    This intentionally does not discover or rank other workspace models. A
    fallback must be selected by the caller so adding a model configuration
    cannot silently change an existing execution path.
    """

    if fallback_config_id == primary_config_id:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "fallback model configuration must differ from the primary model",
        )

    primary = await load_model_gateway_config(session, workspace_id, primary_config_id, cipher)
    if primary[2].kind != "CHAT":
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "the primary configuration is not a chat model",
        )
    if fallback_config_id is None:
        return (primary,)

    fallback = await load_model_gateway_config(session, workspace_id, fallback_config_id, cipher)
    if fallback[2].kind != "CHAT":
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "the fallback configuration is not a chat model",
        )

    primary_endpoint = _endpoint_identity(primary[0].base_url)
    fallback_endpoint = _endpoint_identity(fallback[0].base_url)
    if primary_endpoint == fallback_endpoint and primary[0].model == fallback[0].model:
        raise DomainError(
            FailureCode.VALIDATION_FAILED,
            "fallback model must use a different endpoint or model",
        )
    return (primary, fallback)
