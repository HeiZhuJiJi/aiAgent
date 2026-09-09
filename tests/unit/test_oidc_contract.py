from enterprise_agent.identity.oidc import OIDCAdapter, OIDCIdentity


class _Adapter:
    async def authenticate(self, id_token: str) -> OIDCIdentity:
        assert id_token
        return OIDCIdentity(
            issuer="https://identity.example.test",
            subject="subject-1",
            email="person@example.test",
            email_verified=True,
        )


def test_oidc_adapter_boundary_and_claim_contract() -> None:
    adapter: OIDCAdapter = _Adapter()
    assert adapter is not None
    assert "id_token" in OIDCAdapter.authenticate.__annotations__
    assert OIDCIdentity.model_json_schema()["properties"]["schema_version"]
