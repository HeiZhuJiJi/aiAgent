import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Literal

from enterprise_agent.contracts.domain import ContextBundle, ContextItem

Trust = Literal[
    "TRUSTED_PLATFORM",
    "TRUSTED_WORKSPACE",
    "USER_INPUT",
    "UNTRUSTED_DOCUMENT",
    "UNTRUSTED_WEB",
    "UNTRUSTED_MCP_OUTPUT",
]
TokenCounter = Callable[[str], int]
Compressor = Callable[[str, int], str]


@dataclass(frozen=True)
class ContextCandidate:
    source: str
    content: str
    priority: int
    relevance: float
    trust: Trust
    compressible: bool = True


def _identity(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content).strip().casefold()
    return hashlib.sha256(normalized.encode()).hexdigest()


def build_context(
    candidates: Sequence[ContextCandidate],
    *,
    max_tokens: int,
    token_counter: TokenCounter,
    compressor: Compressor,
    minimum_relevance: float = 0,
) -> ContextBundle:
    if max_tokens <= 0:
        raise ValueError("max_tokens must be positive")
    if not 0 <= minimum_relevance <= 1:
        raise ValueError("minimum_relevance must be between zero and one")
    ranked = sorted(
        enumerate(candidates),
        key=lambda pair: (-pair[1].priority, -pair[1].relevance, pair[0]),
    )
    seen: set[str] = set()
    remaining = max_tokens
    items: list[ContextItem] = []
    for _, candidate in ranked:
        if not 0 <= candidate.relevance <= 1:
            raise ValueError("candidate relevance must be between zero and one")
        if candidate.trust not in {
            "TRUSTED_PLATFORM",
            "TRUSTED_WORKSPACE",
            "USER_INPUT",
            "UNTRUSTED_DOCUMENT",
            "UNTRUSTED_WEB",
            "UNTRUSTED_MCP_OUTPUT",
        }:
            raise ValueError("invalid context trust classification")
        content = candidate.content.strip()
        digest = _identity(content)
        if not content:
            included, reason, tokens = False, "empty", 0
        elif digest in seen:
            included, reason, tokens = False, "duplicate", token_counter(content)
        elif candidate.relevance < minimum_relevance:
            included, reason, tokens = False, "below_relevance_threshold", token_counter(content)
        else:
            seen.add(digest)
            tokens = token_counter(content)
            reason = "selected"
            if tokens > remaining and candidate.compressible and remaining > 0:
                compressed = compressor(content, remaining).strip()
                compressed_tokens = token_counter(compressed)
                if compressed and compressed_tokens <= remaining:
                    content, tokens, reason = compressed, compressed_tokens, "selected_compressed"
            included = tokens <= remaining
            if included:
                remaining -= tokens
            else:
                reason = "token_budget_exceeded"
        items.append(
            ContextItem(
                source=candidate.source,
                content=content,
                priority=candidate.priority,
                token_count=tokens,
                included=included,
                reason=reason,
                trust=candidate.trust,
            )
        )
    return ContextBundle(items=tuple(items), max_tokens=max_tokens)
