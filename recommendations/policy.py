"""The personalised "Recommended for you" policy, shared by serving and evaluation.

One code path builds the candidate list and orders it:

1. ``source_lists`` - five sources, each one implementation parameterised by
   time: collaborative (ALS), content (similar to the anchor product), fbt
   (bought in the same orders as the anchor), trending, and recent activity.
   Model-like sources (ALS, co-purchase) use the snapshot's ``model_as_of``; the
   counting sources use the query moment ``as_of``. ``None`` means now.
2. ``build_candidates`` - drops products the customer already bought and
   products that were not sellable at ``as_of``, takes up to ``PER_SOURCE``
   from each source in order, then fills to ``POOL_SIZE`` by best score.
3. ``final_scores`` - the segment policy: customers with no purchases get the
   list ordered by product popularity; everyone else gets the blend of the
   retrieval score and the min-max-scaled ranker score.

``serve.recommend(..., intent="personalized")`` and ``evaluate.py`` both call
these functions; tests/test_policy_parity.py checks they agree.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from .candidates import collaborative, fbt, popularity
from .candidates.common import available

SOURCES = ("collaborative", "content", "fbt", "trending", "recent_activity")
SOURCE_LIMITS = {"collaborative": 100, "content": 50, "fbt": 50}
PER_SOURCE = 20
POOL_SIZE = 100
RANKER_BLEND = 0.5  # weight of the ranker in the blend for returning customers
RECENT_SEEDS_BOUGHT = 3
RECENT_SEEDS_VIEWED = 2


@dataclass
class CustomerState:
    """What is known about a customer at the query moment (newest first)."""

    customer_id: str | None
    bought: list[str] = field(default_factory=list)
    viewed: list[str] = field(default_factory=list)

    @property
    def anchor(self) -> str | None:
        """The most recent purchase, else the most recent viewed product."""
        if self.bought:
            return self.bought[0]
        return self.viewed[0] if self.viewed else None

    @property
    def seeds(self) -> list[str]:
        return self.bought[:RECENT_SEEDS_BOUGHT] + self.viewed[:RECENT_SEEDS_VIEWED]

    @property
    def first_timer(self) -> bool:
        return not self.bought

    @property
    def history(self) -> list[str]:
        """Profile history for ranker features: purchases first, then views."""
        return self.bought + self.viewed


class Sources:
    """The five candidate sources at one model snapshot (``None`` = serving)."""

    def __init__(self, model_as_of=None, use_content: bool = True):
        self.model_as_of = model_as_of
        self.use_content = use_content
        self._als: dict[str, list[dict]] = {}
        self._content: dict[str, list[dict]] = {}
        self._fbt: dict[str, list[dict]] = {}

    def collaborative(self, customer_id):
        if not customer_id:
            return []
        if customer_id not in self._als:
            self._als[customer_id] = collaborative.get_collaborative(
                customer_id, limit=SOURCE_LIMITS["collaborative"], as_of=self.model_as_of
            )
        return self._als[customer_id]

    def content(self, anchor):
        if not anchor or not self.use_content:
            return []
        if anchor not in self._content:
            from .candidates.content import get_similar_products

            self._content[anchor] = get_similar_products(anchor, limit=SOURCE_LIMITS["content"])
        return self._content[anchor]

    def fbt(self, anchor):
        if not anchor:
            return []
        if anchor not in self._fbt:
            self._fbt[anchor] = fbt.co_purchase(anchor, as_of=self.model_as_of, limit=SOURCE_LIMITS["fbt"])
        return self._fbt[anchor]

    @staticmethod
    def trending(as_of):
        return list(popularity.trending(as_of))

    @staticmethod
    def recent_activity(seeds, as_of):
        return popularity.recent_activity(seeds, as_of)

    @staticmethod
    def popularity(as_of) -> pd.Series:
        return popularity.activity_counts(as_of)


def source_lists(state: CustomerState, sources: Sources, as_of) -> dict[str, list[dict]]:
    anchor = state.anchor
    return {
        "collaborative": sources.collaborative(state.customer_id),
        "content": sources.content(anchor),
        "fbt": sources.fbt(anchor),
        "trending": sources.trending(as_of),
        "recent_activity": sources.recent_activity(state.seeds, as_of),
    }


def build_candidates(
    state: CustomerState,
    sources: Sources,
    as_of=None,
    exclude: set[str] | None = None,
    lists: dict[str, list[dict]] | None = None,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Candidate list (dedup, availability, quotas, fill) and the filtered source lists."""
    lists = source_lists(state, sources, as_of) if lists is None else lists
    excluded = set(state.bought) | set(exclude or ())
    everything = {c["product_id"] for items in lists.values() for c in items} - excluded
    ordered = sorted(everything)
    sellable = {pid for pid, ok in zip(ordered, available(ordered, as_of)) if ok}
    filtered = {
        name: [c for c in items if c["product_id"] in sellable]
        for name, items in lists.items()
    }
    best: dict[str, dict] = {}
    origin: dict[str, set] = {}
    for name in SOURCES:
        for c in filtered.get(name, []):
            pid = c["product_id"]
            origin.setdefault(pid, set()).add(name)
            if float(c["score"]) > best.get(pid, {"score": -1.0})["score"]:
                best[pid] = {**c, "score": float(c["score"]), "source": name}
    selected: dict[str, dict] = {}
    for name in SOURCES:
        taken = 0
        for c in filtered.get(name, []):
            if len(selected) >= POOL_SIZE or taken >= PER_SOURCE:
                break
            pid = c["product_id"]
            if pid not in selected:
                selected[pid] = best[pid]
                taken += 1
    for c in sorted(best.values(), key=lambda c: (-c["score"], c["product_id"])):
        if len(selected) >= POOL_SIZE:
            break
        selected.setdefault(c["product_id"], c)
    pool = [
        {**c, "retrieval_score": c["score"], "n_sources": len(origin[pid])}
        for pid, c in selected.items()
    ]
    return pool, filtered


def final_scores(
    retrieval_scores: np.ndarray,
    popularity_counts: np.ndarray,
    first_timer: bool,
    ranker_scores: np.ndarray | None = None,
) -> np.ndarray:
    """Segment policy for one candidate list."""
    retrieval_scores = np.asarray(retrieval_scores, dtype=float)
    if first_timer:
        pop = np.asarray(popularity_counts, dtype=float)
        peak = pop.max() if len(pop) else 0.0
        return 1.0 + (pop / peak if peak > 0 else np.zeros_like(pop))
    if ranker_scores is None:
        return retrieval_scores
    return blend(retrieval_scores, ranker_scores)


def blend(retrieval_scores, ranker_scores) -> np.ndarray:
    raw = np.asarray(ranker_scores, dtype=float)
    spread = raw.max() - raw.min() if len(raw) else 0.0
    scaled = (raw - raw.min()) / spread if spread > 0 else np.full(len(raw), 0.5)
    return (1 - RANKER_BLEND) * np.asarray(retrieval_scores, dtype=float) + RANKER_BLEND * scaled


def rank(
    state: CustomerState,
    pool: list[dict],
    sources: Sources,
    as_of=None,
    model: Any = None,
    products: pd.DataFrame | None = None,
) -> list[dict]:
    """Order one candidate list with the segment policy (serving entry point)."""
    if not pool:
        return []
    from .ranker import FEATURES, candidate_features, customer_profile, model_uses_current_features

    counts = sources.popularity(as_of)
    frame = pd.DataFrame(pool)
    pop = frame["product_id"].map(counts).fillna(0).to_numpy(dtype=float)
    raw = None
    if model is not None and products is not None and not state.first_timer and model_uses_current_features(model):
        profile = customer_profile(state.history, products, purchase_count=len(state.bought))
        featured = candidate_features(frame, products, profile, counts, state.anchor)
        raw = model.predict(np.ascontiguousarray(featured[FEATURES].to_numpy(dtype=np.float32))).astype(float)
    scores = final_scores(frame["retrieval_score"].to_numpy(), pop, state.first_timer, raw)
    ranked = [{**item, "score": float(s)} for item, s in zip(pool, scores)]
    ranked.sort(key=lambda item: (-item["score"], item["product_id"]))
    return ranked
