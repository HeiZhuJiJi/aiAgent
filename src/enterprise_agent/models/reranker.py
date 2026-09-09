from pydantic import BaseModel, Field

from enterprise_agent.contracts.domain import Classification, FailureCode
from enterprise_agent.domain.states import DomainError
from enterprise_agent.models.gateway import ModelConfig, ModelGateway


class RerankItem(BaseModel):
    index: int = Field(ge=0)
    relevance_score: float


class RerankResponse(BaseModel):
    results: tuple[RerankItem, ...]


class RerankerGateway:
    def __init__(self, gateway: ModelGateway) -> None:
        self._gateway = gateway

    @property
    def config(self) -> ModelConfig:
        return self._gateway.config

    async def score(
        self,
        query: str,
        documents: tuple[str, ...],
        classification: Classification,
    ) -> tuple[float, ...]:
        if not query.strip() or not documents:
            raise DomainError(FailureCode.VALIDATION_FAILED, "rerank input is empty")
        response = await self._gateway._post(
            "rerank",
            {
                "model": self._gateway.config.model,
                "query": query,
                "documents": list(documents),
            },
            classification,
        )
        try:
            parsed = RerankResponse.model_validate(response.json())
        except ValueError:
            raise DomainError(FailureCode.LLM_SCHEMA_ERROR, "invalid reranker response") from None
        by_index = {item.index: item.relevance_score for item in parsed.results}
        if set(by_index) != set(range(len(documents))):
            raise DomainError(FailureCode.LLM_SCHEMA_ERROR, "reranker result indices differ")
        return tuple(float(by_index[index]) for index in range(len(documents)))
