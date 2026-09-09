from enterprise_agent.runtime.context import ContextCandidate, build_context


def words(value: str) -> int:
    return len(value.split())


def truncate(value: str, limit: int) -> str:
    return " ".join(value.split()[:limit])


def test_context_pipeline_prioritizes_deduplicates_compresses_and_budgets() -> None:
    bundle = build_context(
        (
            ContextCandidate(
                source="rag:duplicate",
                content="  verified   policy  ",
                priority=50,
                relevance=0.8,
                trust="UNTRUSTED_DOCUMENT",
            ),
            ContextCandidate(
                source="system",
                content="verified policy",
                priority=100,
                relevance=1,
                trust="TRUSTED_PLATFORM",
                compressible=False,
            ),
            ContextCandidate(
                source="plan",
                content="collect supplier financial operational compliance evidence",
                priority=80,
                relevance=0.9,
                trust="TRUSTED_WORKSPACE",
            ),
            ContextCandidate(
                source="irrelevant",
                content="unrelated conversation",
                priority=70,
                relevance=0.1,
                trust="USER_INPUT",
            ),
        ),
        max_tokens=6,
        token_counter=words,
        compressor=truncate,
        minimum_relevance=0.5,
    )
    assert [item.source for item in bundle.items] == [
        "system",
        "plan",
        "irrelevant",
        "rag:duplicate",
    ]
    assert bundle.items[0].included and bundle.items[0].reason == "selected"
    assert bundle.items[1].included and bundle.items[1].reason == "selected_compressed"
    assert bundle.items[1].token_count == 4
    assert not bundle.items[2].included
    assert bundle.items[2].reason == "below_relevance_threshold"
    assert not bundle.items[3].included and bundle.items[3].reason == "duplicate"
    assert sum(item.token_count for item in bundle.items if item.included) == 6


def test_context_budget_records_excluded_uncompressible_candidate() -> None:
    bundle = build_context(
        (
            ContextCandidate(
                source="policy",
                content="one two three",
                priority=2,
                relevance=1,
                trust="TRUSTED_PLATFORM",
                compressible=False,
            ),
            ContextCandidate(
                source="goal",
                content="four five",
                priority=1,
                relevance=1,
                trust="USER_INPUT",
                compressible=False,
            ),
        ),
        max_tokens=3,
        token_counter=words,
        compressor=truncate,
    )
    assert bundle.items[0].included
    assert not bundle.items[1].included
    assert bundle.items[1].reason == "token_budget_exceeded"


def test_context_rejects_invalid_relevance_or_trust() -> None:
    invalid = ContextCandidate("x", "content", 1, 1.1, "USER_INPUT")
    try:
        build_context((invalid,), max_tokens=2, token_counter=words, compressor=truncate)
    except ValueError as error:
        assert "relevance" in str(error)
    else:
        raise AssertionError("invalid relevance accepted")
