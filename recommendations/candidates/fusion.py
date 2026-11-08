def merge_candidates(
    *sources: list[dict],
    limit: int = 300,
    weights: dict[str, float] | None = None,
) -> list[dict]:
    """Weight and deduplicate candidates while preserving their winning source."""
    best: dict[str, dict] = {}
    source_weights = weights or {}
    for source in sources:
        for candidate in source:
            product_id = candidate.get("product_id")
            if not product_id:
                continue
            item = candidate.copy()
            raw_score = float(item.get("score", 0.0))
            item["retrieval_score"] = raw_score
            item["score"] = raw_score * source_weights.get(item.get("source"), 1.0)
            current = best.get(product_id)
            if current is None or item["score"] > current["score"]:
                best[product_id] = item
    return sorted(
        best.values(), key=lambda item: (-float(item["score"]), item["product_id"])
    )[:limit]
