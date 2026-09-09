from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, Field, SecretStr, field_validator, model_validator

from enterprise_agent.contracts.domain import Contract
from enterprise_agent.governance.policy import Role


class RegisterInput(Contract):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr

    @field_validator("email")
    @classmethod
    def valid_email(cls, value: str) -> str:
        normalized = value.strip().casefold()
        local, separator, domain = normalized.partition("@")
        if not separator or not local or "." not in domain or domain.startswith("."):
            raise ValueError("invalid email address")
        return value.strip()

    @model_validator(mode="after")
    def valid_password(self) -> Self:
        password = self.password.get_secret_value()
        if len(password) < 12 or len(password) > 1024:
            raise ValueError("password must contain between 12 and 1024 characters")
        return self


class LoginInput(Contract):
    email: str = Field(min_length=3, max_length=320)
    password: SecretStr


class UserView(Contract):
    user_id: UUID
    email: str
    enabled: bool
    created_at: AwareDatetime


class SessionView(Contract):
    access_token: str
    token_type: str = "bearer"
    expires_at: AwareDatetime


class Identity(Contract):
    user_id: UUID
    email: str
    session_id: UUID
    expires_at: AwareDatetime


class WorkspaceCreate(Contract):
    name: str = Field(min_length=1, max_length=200)

    @field_validator("name")
    @classmethod
    def clean_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("workspace name cannot be blank")
        return cleaned


class WorkspaceView(Contract):
    workspace_id: UUID
    name: str
    role: Role
    enabled: bool
    version: int
    created_at: datetime | None = None


class RetentionPolicyUpdate(Contract):
    expected_version: int = Field(gt=0)
    conversation_retention_days: int = Field(ge=1, le=36_500)
    trace_retention_days: int = Field(ge=1, le=36_500)
    artifact_retention_days: int = Field(ge=1, le=36_500)
    document_retention_days: int = Field(ge=1, le=36_500)
    evaluation_retention_days: int = Field(ge=1, le=36_500)
    audit_retention_days: int | None = Field(default=None, ge=1, le=36_500)
    retain_audit_tombstone: bool = True


class RetentionPolicyView(Contract):
    workspace_id: UUID
    version: int
    conversation_retention_days: int
    trace_retention_days: int
    artifact_retention_days: int
    document_retention_days: int
    evaluation_retention_days: int
    audit_retention_days: int | None
    retain_audit_tombstone: bool
    updated_at: datetime


class WorkspaceDeletionRequest(Contract):
    expected_version: int = Field(gt=0)
    retain_audit_tombstone: bool = True


class WorkspaceDeletionView(Contract):
    request_id: UUID
    workspace_id: UUID
    state: str
    version: int
    active_task_count: int = Field(ge=0)
    safe_error: str | None
    requested_at: datetime
    completed_at: datetime | None
