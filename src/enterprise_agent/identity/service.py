import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.governance.policy import Role
from enterprise_agent.identity.contracts import (
    Identity,
    LoginInput,
    RegisterInput,
    SessionView,
    UserView,
    WorkspaceCreate,
    WorkspaceView,
)
from enterprise_agent.storage.tables import (
    AuditRow,
    AuthSessionRow,
    RetentionPolicyRow,
    UserRow,
    Workspace,
    WorkspaceMemberRow,
)

PASSWORD_HASHER = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
SESSION_DURATION = timedelta(hours=12)


def normalized_email(value: str) -> str:
    return value.strip().casefold()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def user_view(row: UserRow) -> UserView:
    return UserView(
        user_id=row.id,
        email=row.email,
        enabled=row.enabled,
        created_at=row.created_at,
    )


async def register_user(session: AsyncSession, value: RegisterInput) -> UserView:
    user_id = uuid4()
    created = await session.scalar(
        insert(UserRow)
        .values(
            id=user_id,
            email=value.email,
            normalized_email=normalized_email(value.email),
            password_hash=PASSWORD_HASHER.hash(value.password.get_secret_value()),
        )
        .on_conflict_do_nothing(index_elements=["normalized_email"])
        .returning(UserRow.id)
    )
    if created is None:
        raise DomainError(FailureCode.VALIDATION_FAILED, "email already registered")
    row = await session.get(UserRow, user_id)
    if row is None:
        raise DomainError(FailureCode.CONSISTENCY_VIOLATION, "registered user disappeared")
    return user_view(row)


async def issue_session(session: AsyncSession, value: LoginInput) -> SessionView:
    row = await session.scalar(
        select(UserRow).where(UserRow.normalized_email == normalized_email(value.email))
    )
    if row is None or not row.enabled:
        raise DomainError(FailureCode.POLICY_DENIED, "invalid credentials")
    try:
        PASSWORD_HASHER.verify(row.password_hash, value.password.get_secret_value())
    except (VerificationError, InvalidHashError) as exc:
        raise DomainError(FailureCode.POLICY_DENIED, "invalid credentials") from exc
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(UTC) + SESSION_DURATION
    session.add(
        AuthSessionRow(
            id=uuid4(),
            user_id=row.id,
            token_hash=token_hash(token),
            expires_at=expires_at,
        )
    )
    await session.flush()
    return SessionView(access_token=token, expires_at=expires_at)


async def authenticate(session: AsyncSession, token: str) -> Identity:
    now = await session.scalar(select(func.clock_timestamp()))
    if now is None:
        raise DomainError(FailureCode.DB_UNAVAILABLE, "database clock unavailable")
    result = await session.execute(
        select(AuthSessionRow, UserRow)
        .join(UserRow, UserRow.id == AuthSessionRow.user_id)
        .where(
            AuthSessionRow.token_hash == token_hash(token),
            AuthSessionRow.revoked_at.is_(None),
            AuthSessionRow.expires_at > now,
            UserRow.enabled.is_(True),
        )
    )
    pair = result.one_or_none()
    if pair is None:
        raise DomainError(FailureCode.POLICY_DENIED, "invalid or expired session")
    auth, user = pair
    return Identity(
        user_id=user.id,
        email=user.email,
        session_id=auth.id,
        expires_at=auth.expires_at,
    )


async def revoke_session(session: AsyncSession, identity: Identity) -> None:
    row = await session.get(AuthSessionRow, identity.session_id)
    if row is None or row.user_id != identity.user_id:
        raise DomainError(FailureCode.POLICY_DENIED, "session unavailable")
    if row.revoked_at is None:
        row.revoked_at = datetime.now(UTC)
        await session.flush()


async def create_workspace(
    session: AsyncSession, identity: Identity, value: WorkspaceCreate, trace_id: str
) -> WorkspaceView:
    workspace = Workspace(id=uuid4(), name=value.name, enabled=True, version=1)
    session.add(workspace)
    await session.flush()
    session.add(RetentionPolicyRow(workspace_id=workspace.id))
    session.add(
        WorkspaceMemberRow(
            id=uuid4(),
            workspace_id=workspace.id,
            user_id=identity.user_id,
            role=Role.WORKSPACE_OWNER.value,
        )
    )
    session.add(
        AuditRow(
            workspace_id=workspace.id,
            actor_type="USER",
            actor_id=identity.user_id,
            action="workspace.created",
            resource_type="WORKSPACE",
            resource_id=workspace.id,
            result={"version": 1},
            trace_id=trace_id,
        )
    )
    await session.flush()
    return WorkspaceView(
        workspace_id=workspace.id,
        name=workspace.name,
        role=Role.WORKSPACE_OWNER,
        enabled=workspace.enabled,
        version=workspace.version,
    )


async def list_workspaces(session: AsyncSession, identity: Identity) -> tuple[WorkspaceView, ...]:
    rows = (
        await session.execute(
            select(Workspace, WorkspaceMemberRow)
            .join(WorkspaceMemberRow, WorkspaceMemberRow.workspace_id == Workspace.id)
            .where(
                WorkspaceMemberRow.user_id == identity.user_id,
                Workspace.enabled.is_(True),
            )
            .order_by(Workspace.name, Workspace.id)
        )
    ).all()
    return tuple(
        WorkspaceView(
            workspace_id=workspace.id,
            name=workspace.name,
            role=Role(member.role),
            enabled=workspace.enabled,
            version=workspace.version,
        )
        for workspace, member in rows
    )


async def get_workspace(
    session: AsyncSession, identity: Identity, workspace_id: UUID
) -> WorkspaceView:
    pair = (
        await session.execute(
            select(Workspace, WorkspaceMemberRow)
            .join(WorkspaceMemberRow, WorkspaceMemberRow.workspace_id == Workspace.id)
            .where(
                Workspace.id == workspace_id,
                WorkspaceMemberRow.user_id == identity.user_id,
                Workspace.enabled.is_(True),
            )
        )
    ).one_or_none()
    if pair is None:
        raise DomainError(FailureCode.POLICY_DENIED, "workspace unavailable")
    workspace, member = pair
    return WorkspaceView(
        workspace_id=workspace.id,
        name=workspace.name,
        role=Role(member.role),
        enabled=workspace.enabled,
        version=workspace.version,
    )
