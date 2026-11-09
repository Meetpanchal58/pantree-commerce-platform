"""The personalised "Recommended for you" policy, shared by serving and evaluation.

One code path builds the candidate list and orders it:

1. ``source_lists`` - five sources, each one implementation parameterised by
   time: collaborative (ALS), content (similar to the anchor product), fbt
   (bought in the same orders as the anchor), trending, and recent activity.
   Model-like sources (ALS, co-purchase) use the snapshot's ``model_as_of``; the
   counting sources use the query moment ``as_of``. ``None`` means now.
2. ``build_candidates`` - drops products the customer already bought and
   products that were not sellable at ``as_of``, takes each source's quota in
   order, then fills to ``pool_size`` by best score.
3. ``final_scores`` - the segment policy: customers with no purchases get the
   list ordered by product popularity (if ``first_timer_popularity``); everyone
   else gets the blend of the retrieval score and the min-max-scaled ranker score.

The tunable settings live in one ``PolicyConfig``, read from
``recommendations/reports/policy_config.json`` (chosen on the validation period
by ``recommendations/tune_policy.py``); serving and evaluation both use
``load_config()``.

``serve.recommend(..., intent="personalized")`` and ``evaluate.py`` both call
these functions; tests/test_policy_parity.py checks they agree.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from .candidates import collaborative, fbt, popularity
from .candidates.common import available

logger = logging.getLogger(__name__)
SOURCES = ("collaborative", "content", "fbt", "trending", "recent_activity")
SOURCE_LIMITS = {"collaborative": 100, "content": 50, "fbt": 50}
POLICY_CONFIG_PATH = Path(__file__).with_name("reports") / "policy_config.json"


@dataclass(frozen=True)
class PolicyConfig:
    """Every tunable setting of the personalised policy."""

    quotas: tuple = (20, 20, 20, 20, 20)  # per source, in SOURCES order
    pool_size: int = 100
    ranker_blend: float = 0.5  # weight of the ranker vs the retrieval score
    first_timer_popularity: bool = True  # customers with no purchases: popularity order
    trending_window_days: int = 30

    @staticmethod
    def with_collaborative_quota(collaborative: int, pool_size: int, **kwargs) -> "PolicyConfig":
        """ALS gets ``collaborative`` slots; the rest of the pool is split evenly."""
        rest = (pool_size - collaborative) // (len(SOURCES) - 1)
        return PolicyConfig(quotas=(collaborative,) + (rest,) * (len(SOURCES) - 1), pool_size=pool_size, **kwargs)

    def to_json(self) -> dict:
        out = asdict(self)
        out["quotas"] = dict(zip(SOURCES, self.quotas))
        return out

    @staticmethod
    def from_json(data: dict) -> "PolicyConfig":
        quotas = data["quotas"]
        if isinstance(quotas, dict):
            quotas = tuple(int(quotas[name]) for name in SOURCES)
        return PolicyConfig(
            quotas=tuple(int(q) for q in quotas), pool_size=int(data["pool_size"]),
            ranker_blend=float(data["ranker_blend"]),
            first_timer_popularity=bool(data["first_timer_popularity"]),
            trending_window_days=int(data["trending_window_days"]),
        )


DEFAULT_CONFIG = PolicyConfig()


@lru_cache(maxsize=1)
def load_config() -> PolicyConfig:
    """The settings chosen on validation; the defaults (with a warning) if absent."""
    try:
        return PolicyConfig.from_json(json.loads(POLICY_CONFIG_PATH.read_text())["chosen"])
    except (OSError, KeyError, ValueError, TypeError) as exc:
        logger.warning("Using default policy settings; could not read %s (%s)", POLICY_CONFIG_PATH, exc)
        return DEFAULT_CONFIG


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

    def __init__(self, model_as_of=None, use_content: bool = True, trending_window_days: int | None = None):
        self.model_as_of = model_as_of
        self.use_content = use_content
        self.trending_window_days = trending_window_days or DEFAULT_CONFIG.trending_window_days
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

    def trending(self, as_of):
        return list(popularity.trending(as_of, window_days=self.trending_window_days))

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
    config: PolicyConfig = DEFAULT_CONFIG,
) -> tuple[list[dict], dict[str, list[dict]]]:
    """Candidate list (dedup, availability, quotas, fill) and the filtered source lists.

    A source missing from ``lists`` contributes nothing (used by the ablation).
    """
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
    for name, quota in zip(SOURCES, config.quotas):
        taken = 0
        for c in filtered.get(name, []):
            if len(selected) >= config.pool_size or taken >= quota:
                break
            pid = c["product_id"]
            if pid not in selected:
                selected[pid] = best[pid]
                taken += 1
    for c in sorted(best.values(), key=lambda c: (-c["score"], c["product_id"])):
        if len(selected) >= config.pool_size:
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
    config: PolicyConfig = DEFAULT_CONFIG,
) -> np.ndarray:
    """Segment policy for one candidate list."""
    retrieval_scores = np.asarray(retrieval_scores, dtype=float)
    if first_timer and config.first_timer_popularity:
        pop = np.asarray(popularity_counts, dtype=float)
        peak = pop.max() if len(pop) else 0.0
        return 1.0 + (pop / peak if peak > 0 else np.zeros_like(pop))
    if ranker_scores is None:
        return retrieval_scores
    return blend(retrieval_scores, ranker_scores, config.ranker_blend)


def blend(retrieval_scores, ranker_scores, weight: float = DEFAULT_CONFIG.ranker_blend) -> np.ndarray:
    """(1 - weight) x retrieval score + weight x min-max-scaled ranker score."""
    raw = np.asarray(ranker_scores, dtype=float)
    spread = raw.max() - raw.min() if len(raw) else 0.0
    scaled = (raw - raw.min()) / spread if spread > 0 else np.full(len(raw), 0.5)
    return (1 - weight) * np.asarray(retrieval_scores, dtype=float) + weight * scaled


def rank(
    state: CustomerState,
    pool: list[dict],
    sources: Sources,
    as_of=None,
    model: Any = None,
    products: pd.DataFrame | None = None,
    config: PolicyConfig = DEFAULT_CONFIG,
) -> list[dict]:
    """Order one candidate list with the segment policy (serving entry point)."""
    if not pool:
        return []
    from .ranker import FEATURES, candidate_features, customer_profile, model_uses_current_features

    counts = sources.popularity(as_of)
    frame = pd.DataFrame(pool)
    pop = frame["product_id"].map(counts).fillna(0).to_numpy(dtype=float)
    raw = None
    needs_ranker = not (state.first_timer and config.first_timer_popularity)
    if model is not None and products is not None and needs_ranker and model_uses_current_features(model):
        profile = customer_profile(state.history, products, purchase_count=len(state.bought))
        featured = candidate_features(frame, products, profile, counts, state.anchor)
        raw = model.predict(np.ascontiguousarray(featured[FEATURES].to_numpy(dtype=np.float32))).astype(float)
    scores = final_scores(frame["retrieval_score"].to_numpy(), pop, state.first_timer, raw, config)
    ranked = [{**item, "score": float(s)} for item, s in zip(pool, scores)]
    ranked.sort(key=lambda item: (-item["score"], item["product_id"]))
    return ranked
