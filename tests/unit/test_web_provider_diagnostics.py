import httpx
import pytest
from pydantic import SecretStr

from enterprise_agent.contracts.domain import FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.runtime.web_provider import HttpWebProvider, WebProviderConfig


@pytest.mark.parametrize("structured", [True, False])
async def test_search_failure_preserves_bounded_diagnostics_without_accepting_results(structured):
    response = (httpx.Response(503, json={"detail": {"backend_diagnostics": [
        {"engine": "engine-a", "reason": "rate limited"}]}}) if structured
        else httpx.Response(503, text="private upstream error page"))
    config = WebProviderConfig(base_url="https://search.example/v1", api_key=SecretStr("unused"), external=True)
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: response)) as client:
        provider = HttpWebProvider(config, client)
        with pytest.raises(DomainError) as error:
            await provider.search("Alpha inventory", 3)
    assert error.value.code is FailureCode.CAPABILITY_UNAVAILABLE
    assert "HTTP 503" in str(error.value)
    assert "private upstream error page" not in str(error.value)
    assert provider.backend_diagnostics == ([{"engine": "engine-a", "reason": "rate limited"}] if structured else [])
