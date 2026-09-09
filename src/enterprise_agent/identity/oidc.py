from typing import Protocol

from pydantic import Field

from enterprise_agent.contracts.domain import Contract


class OIDCIdentity(Contract):
    issuer: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    email: str = Field(min_length=3, max_length=320)
    email_verified: bool


class OIDCAdapter(Protocol):
    """Boundary for a future enterprise OIDC provider implementation."""

    async def authenticate(self, id_token: str) -> OIDCIdentity:
        """Validate a provider token and return normalized identity claims."""
        ...
