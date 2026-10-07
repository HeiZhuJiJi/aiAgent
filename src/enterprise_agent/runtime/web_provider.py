import os
from ipaddress import ip_address
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field, SecretStr

from enterprise_agent.contracts.domain import Contract, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.discovery_pool import bounded_labels, diverse_pool


class WebSearchResult(Contract):
    fetch_truncated: bool = False
    read_mode: str = "UNKNOWN"
    engines: tuple[str, ...] = Field(default=(), max_length=16, exclude=True)
    queries: tuple[str, ...] = Field(default=(), max_length=8, exclude=True)
    title: str
    url: str
    snippet: str
    published_at: str | None = None
    observed_at: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_links: tuple[tuple[str, str], ...] = Field(default=(), max_length=2000, exclude=True)


class WebDiscoveryCandidate(Contract):
    engines: tuple[str, ...] = Field(default=(), max_length=16)
    queries: tuple[str, ...] = Field(default=(), max_length=8)
    url: str = Field(max_length=4000)
    title: str = Field(max_length=500)
    snippet: str = Field(max_length=1200)
    reason: str = Field(max_length=100)


class WebFetchResult(Contract):
    fetch_truncated: bool = False
    read_mode: str = "UNKNOWN"
    url: str
    content: str
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")

    # Discovery hints only; never part of source content or factual support.
    source_links: tuple[tuple[str, str], ...] = Field(default=(), max_length=2000)


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
        self.discovery_candidates: list[WebDiscoveryCandidate] = []
        self.backend_diagnostics: list[dict[str, str]] = []

    def _headers(self) -> dict[str, str]:
        secret = self.config.api_key.get_secret_value()
        return {"Authorization": "Bearer " + secret} if secret else {}

    async def search(self, query: str, limit: int) -> tuple[WebSearchResult, ...]:
        try:
            response = await self._client.post(
                self.config.base_url + "/search",
                json={"query": query, "limit": limit, "queries_managed": True},
                headers=self._headers(),
                timeout=self.config.timeout_sec,
                follow_redirects=False,
            )
        except (httpx.TimeoutException, httpx.TransportError):
            raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE, "Web search unavailable") from None
        if response.is_error or response.is_redirect:
            try:
                body = response.json()
            except ValueError:
                body = {}
            detail = body.get("detail", {}) if isinstance(body, dict) else {}
            diagnostics = detail.get("backend_diagnostics", []) if isinstance(detail, dict) else []
            if isinstance(diagnostics, list):
                for item in diagnostics[:20]:
                    if isinstance(item, dict) and isinstance(item.get("engine"), str) and isinstance(item.get("reason"), str):
                        safe = {"engine": item["engine"][:80], "reason": item["reason"][:160]}
                        if safe not in self.backend_diagnostics and len(self.backend_diagnostics) < 20:
                            self.backend_diagnostics.append(safe)
            reason = "; ".join(item["engine"] + ": " + item["reason"] for item in self.backend_diagnostics)[:800]
            raise DomainError(FailureCode.CAPABILITY_UNAVAILABLE,
                f"Web search rejected (HTTP {response.status_code})" + (": " + reason if reason else ""))
        try:
            payload = response.json()
            values = payload["results"] if isinstance(payload, dict) else payload
            results = tuple(WebSearchResult.model_validate(item) for item in values)
            if isinstance(payload, dict):
                diagnostics = payload.get("backend_diagnostics", [])
                if isinstance(diagnostics, list):
                    for item in diagnostics[:20]:
                        if isinstance(item, dict) and isinstance(item.get("engine"), str) and isinstance(item.get("reason"), str):
                            safe = {"engine": item["engine"][:80], "reason": item["reason"][:160]}
                            if safe not in self.backend_diagnostics and len(self.backend_diagnostics) < 20:
                                self.backend_diagnostics.append(safe)
                raw_candidates = payload.get("candidates", [])
                if isinstance(raw_candidates, list):
                    candidates = {item.url: item for item in self.discovery_candidates}
                    for item in raw_candidates[:40]:
                        try:
                            candidate = WebDiscoveryCandidate.model_validate(item)
                        except ValueError:
                            continue
                        old = candidates.get(candidate.url)
                        if old is not None:
                            chosen = candidate if len(candidate.snippet) > len(old.snippet) else old
                            candidate = chosen.model_copy(update={
                                "engines": bounded_labels((*old.engines, *candidate.engines), 16, 80),
                                "queries": bounded_labels((*old.queries, *candidate.queries), 8, 1000),
                            })
                        candidates[candidate.url] = candidate
                    self.discovery_candidates = diverse_pool(list(candidates.values()), 80,
                        lambda item: item.queries or item.engines)
            return results
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
