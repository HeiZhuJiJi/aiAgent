import os
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, SecretStr

from enterprise_agent.contracts.domain import Contract, FailureCode
from enterprise_agent.domain.states import DomainError


class WebSearchResult(Contract):
    title: str
    url: str
    snippet: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class WebFetchResult(Contract):
    url: str
    content: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class WebProviderConfig(BaseModel):
    base_url: str
    api_key: SecretStr
    external: bool
    timeout_sec: float = Field(default=30, gt=0)


def load_web_provider_config() -> WebProviderConfig:
    mode = os.environ.get("WEB_MODE", "external").strip().casefold()
    if mode not in {"local", "external"}:
        raise ValueError("WEB_MODE must be local or external")
    base_url = os.environ.get("WEB_BASE_URL", "").strip().rstrip("/")
    api_key = os.environ.get("WEB_API_KEY", "").strip()
    if not base_url or (mode == "external" and not api_key):
        raise ValueError("WEB_BASE_URL and external WEB_API_KEY must be configured")
    parsed = urlsplit(base_url)
    if (
        parsed.scheme not in ({"http", "https"} if mode == "local" else {"https"})
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("WEB_BASE_URL is invalid")
    if mode == "local":
        try:
            loopback = (
                parsed.hostname in {"localhost", "host.docker.internal"}
                or ip_address(parsed.hostname).is_loopback
            )
        except ValueError:
            loopback = False
        if not loopback:
            raise ValueError(
                "local Web provider must use a loopback or Docker host address"
            )
    return WebProviderConfig(
        base_url=base_url,
        api_key=SecretStr(api_key),
        external=mode == "external",
    )


class HttpWebProvider:
    def __init__(self, config: WebProviderConfig, client: httpx.AsyncClient) -> None:
        self.config = config
        self._client = client

    def _headers(self) -> dict[str, str]:
        secret = self.config.api_key.get_secret_value()
        return {"Authorization": "Bearer " + secret} if secret else {}

    async def search(self, query: str, limit: int) -> tuple[WebSearchResult, ...]:
        try:
            response = await self._client.post(
                self.config.base_url + "/search",
                json={"query": query, "limit": limit},
                headers=self._headers(),
                timeout=self.config.timeout_sec,
                follow_redirects=False,
            )
        except (httpx.TimeoutException, httpx.TransportError):
            raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "Web search unavailable") from None
        if response.is_error or response.is_redirect:
            raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "Web search rejected")
        try:
            payload = response.json()
            values = payload["results"] if isinstance(payload, dict) else payload
            return tuple(WebSearchResult.model_validate(item) for item in values)
        except (KeyError, TypeError, ValueError):
            raise DomainError(FailureCode.CAPABILITY_BAD_OUTPUT, "invalid Web search response") from None

    async def fetch(self, url: str) -> WebFetchResult:
        try:
            response = await self._client.post(
                self.config.base_url + "/fetch",
                json={"url": url},
                headers=self._headers(),
                timeout=self.config.timeout_sec,
                follow_redirects=False,
            )
        except (httpx.TimeoutException, httpx.TransportError):
            raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "Web fetch unavailable") from None
        if response.is_error or response.is_redirect:
            raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "Web fetch rejected")
        try:
            return WebFetchResult.model_validate(response.json())
        except ValueError:
            raise DomainError(FailureCode.CAPABILITY_BAD_OUTPUT, "invalid Web fetch response") from None
