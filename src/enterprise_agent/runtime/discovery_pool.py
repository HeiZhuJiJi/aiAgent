"""Bounded discovery diversity; engine agreement is not factual corroboration."""
from collections.abc import Callable, Iterable


def bounded_labels(values: Iterable[str], limit: int, width: int) -> tuple[str, ...]:
    return tuple(dict.fromkeys(value.strip()[:width] for value in values if value.strip()))[:limit]


def diverse_pool[T](items: list[T], limit: int, origins: Callable[[T], tuple[str, ...]]) -> list[T]:
    """Round-robin ranked engine/query buckets without duplicating a candidate."""
    buckets: dict[str, list[int]] = {}
    for index, item in enumerate(items):
        for origin in origins(item) or ("unattributed",):
            buckets.setdefault(origin, []).append(index)
    selected: set[int] = set()
    result: list[T] = []
    while len(result) < limit:
        changed = False
        for bucket in buckets.values():
            while bucket and bucket[0] in selected:
                bucket.pop(0)
            if bucket and len(result) < limit:
                index = bucket.pop(0)
                selected.add(index)
                result.append(items[index])
                changed = True
        if not changed:
            break
    return result
