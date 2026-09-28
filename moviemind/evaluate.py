"""
Evaluation metrics and the leave-one-out protocol.

**Read docs/phase-04-recommendation-evaluation.md sec 2 before changing
anything here.** Two facts constrain the whole design:

1. *There is no relevance ground truth.* No users, no interactions, no
   judgements. Every label-based metric scores agreement with a structural
   proxy, never user satisfaction.
2. *Every available proxy is correlated with genre.* Within-franchise genre
   Jaccard is 0.775 against 0.149 for random pairs. So the mandatory
   genre-weight experiment cannot be evaluated on a genre-independent signal,
   because none exists in this dataset.

Consequently this module refuses to produce a single headline score. Each
label is reported separately, label-free measures are reported alongside them,
and popularity correlation is computed as a diagnostic that must not be
optimised. Where the criteria disagree, that disagreement is the result.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

import numpy as np

from .labels import LABEL_KINDS, LabelKind, ProxyLabels

#: Evaluation cutoffs. Small because a person will not scroll a film list, and
#: because a larger K washes out exactly the differences being measured.
DEFAULT_KS: tuple[int, ...] = (5, 10, 20)


@dataclass(frozen=True)
class MetricDefinition:
    """What a metric means here, and what it does not."""

    name: str
    definition: str
    measures: str
    does_not_measure: str
    requires_labels: bool


#: Included in the report verbatim so no metric is reported without its
#: caveat travelling with it.
METRIC_DEFINITIONS: tuple[MetricDefinition, ...] = (
    MetricDefinition(
        name="label_recall@K",
        definition=(
            "Mean over query films of |top-K AND relevant| / |relevant|, where "
            "`relevant` is the proxy label's group minus the query itself."
        ),
        measures=(
            "Whether films the proxy label says belong together are retrieved. "
            "For the franchise label this is close to a real relevance claim: "
            "TMDb asserts those films are one series."
        ),
        does_not_measure=(
            "User satisfaction. It is bounded above by 1.0 only for the 528 "
            "films in a multi-film franchise, and it is meaningless for the "
            "~95% of the catalogue with no group."
        ),
        requires_labels=True,
    ),
    MetricDefinition(
        name="label_hit_rate@K",
        definition="Fraction of query films with at least one relevant film in the top-K.",
        measures="How often a label-bearing query finds anything at all.",
        does_not_measure=(
            "Recall magnitude. A query with 1 relevant film and a query with 9 "
            "both count as one hit, which is why it is reported next to recall "
            "and not instead of it."
        ),
        requires_labels=True,
    ),
    MetricDefinition(
        name="label_ndcg@K",
        definition=(
            "Binary-gain NDCG@K over the proxy label, with a single relevant "
            "level. Reported because the brief asks for it and because it "
            "weights position, unlike recall."
        ),
        measures="Ranking quality against the proxy, position-aware.",
        does_not_measure=(
            "Anything recall does not. With binary gains and few relevant "
            "items it is close to a monotone transform of hit position, and it "
            "inherits every limitation of the label."
        ),
        requires_labels=True,
    ),
    MetricDefinition(
        name="catalogue_coverage@K",
        definition=(
            "|union of all top-K lists| / N, over every query in the protocol."
        ),
        measures=(
            "How much of the catalogue the system is able to surface at all. "
            "Label-free, and the single most useful guard against having built "
            "a genre lookup: a genre lookup has near-zero coverage."
        ),
        does_not_measure="Quality. Full coverage can be reached by returning noise.",
        requires_labels=False,
    ),
    MetricDefinition(
        name="intra_list_diversity@K",
        definition=(
            "Mean pairwise 1 - cosine among each query's K recommendations, "
            "measured in the configuration's own vector space."
        ),
        measures="Whether a list is repetitive.",
        does_not_measure=(
            "Diversity in any absolute sense. It is computed in the same "
            "weighted space being tuned, so raising a field's weight lowers it "
            "by construction. It is a within-configuration comparator only."
        ),
        requires_labels=False,
    ),
    MetricDefinition(
        name="distinct_genres@K",
        definition=(
            "Mean number of distinct true genre labels across each top-K list. "
            "Computed from TMDb genres, never from the vector, so it is "
            "independent of the field weights."
        ),
        measures="Genre breadth of a recommendation list, weight-free.",
        does_not_measure="Quality. A broad list can be entirely irrelevant.",
        requires_labels=False,
    ),
    MetricDefinition(
        name="distinct_decades@K",
        definition="Mean number of distinct release decades in each top-K list.",
        measures="Era breadth of a recommendation list, weight-free.",
        does_not_measure="Quality.",
        requires_labels=False,
    ),
    MetricDefinition(
        name="mean_top1_score",
        definition="Mean cosine of the highest-scoring neighbour over all queries.",
        measures=(
            "How tightly the catalogue clusters. Useful for detecting that a "
            "configuration produces near-duplicates."
        ),
        does_not_measure=(
            "Quality. Scores are not calibrated across configurations, and "
            "measured here 80% of queries have a top-1 below 0.30, so a high "
            "score usually means one shared rare term, not a good match."
        ),
        requires_labels=False,
    ),
    MetricDefinition(
        name="popularity_spearman",
        definition=(
            "Spearman rank correlation between a recommendation's position in "
            "the top-K and its TMDb popularity."
        ),
        measures="Whether the system is popularity-biased.",
        does_not_measure=(
            "Quality. This is a DIAGNOSTIC and is never optimised. The "
            "snapshot is already popularity-skewed (Phase 2 sec 15), so a high "
            "value may reflect the sample, not the recommender."
        ),
        requires_labels=False,
    ),
    MetricDefinition(
        name="mean_recommendation_popularity_pct",
        definition=(
            "Mean popularity percentile of recommended films. 0 is the least "
            "popular film in the snapshot, 100 the most."
        ),
        measures=(
            "Absolute novelty tilt. A value near 50 means the system is not "
            "systematically steering to blockbusters, which is the honest goal "
            "for a 'find films like this one' tool."
        ),
        does_not_measure="Quality.",
        requires_labels=False,
    ),
    MetricDefinition(
        name="thin_evidence_rate@K",
        definition=(
            "Fraction of recommendations sharing 3 or fewer non-zero terms "
            "with the query. The threshold is the p75 of the measured "
            "top-1 shared-term distribution."
        ),
        measures=(
            "How often a recommendation rests on almost no evidence. Measured "
            "at 35% of top-1 matches before any tuning, so this is the metric "
            "that exposed the real failure mode."
        ),
        does_not_measure="Quality directly, but bounds how much of any score can be trusted.",
        requires_labels=False,
    ),
)

THIN_EVIDENCE_TERMS = 3

#: How much deeper than the deepest cutoff the candidate pool is fetched when an
#: evidence filter is active, so the filter can promote a passing candidate from
#: below the cutoff instead of only deleting from it.
EVIDENCE_POOL_FACTOR = 8


@dataclass
class LabelScores:
    """Per-label, per-K scores. Never merged across labels."""

    kind: LabelKind
    k: int
    queries_evaluated: int
    recall: float
    hit_rate: float
    ndcg: float
    mean_relevant: float
    #: Mean |relevant AND top-K| / |relevant| over queries with >=1 relevant.
    recall_at_1: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.kind,
            "k": self.k,
            "queries_evaluated": self.queries_evaluated,
            "mean_relevant_per_query": round(self.mean_relevant, 4),
            "recall": round(self.recall, 4),
            "hit_rate": round(self.hit_rate, 4),
            "ndcg": round(self.ndcg, 4),
        }


@dataclass
class EvaluationResult:
    """Everything one configuration scored, in one place."""

    name: str
    k: int
    queries: int
    catalogue: int
    label_scores: dict[str, list[LabelScores]] = field(default_factory=dict)
    coverage: float = 0.0
    intra_list_diversity: float = 0.0
    distinct_genres: float = 0.0
    distinct_decades: float = 0.0
    mean_top1_score: float = 0.0
    popularity_spearman: float = 0.0
    mean_recommendation_popularity_pct: float = 0.0
    thin_evidence_rate: float = 0.0
    unevaluable_queries: dict[str, int] = field(default_factory=dict)
    mean_recommendations_returned: float = 0.0

    def label_recall(self, kind: LabelKind) -> float | None:
        scores = self.label_scores.get(kind)
        if not scores:
            return None
        return next((s.recall for s in scores if s.k == self.k), None)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "k": self.k,
            "queries": self.queries,
            "catalogue": self.catalogue,
            "mean_recommendations_returned": round(self.mean_recommendations_returned, 3),
            "label_scores": {
                kind: [s.as_dict() for s in scores]
                for kind, scores in sorted(self.label_scores.items())
            },
            "unevaluable_queries": dict(sorted(self.unevaluable_queries.items())),
            "label_free": {
                "catalogue_coverage": round(self.coverage, 4),
                "intra_list_diversity": round(self.intra_list_diversity, 4),
                "distinct_genres": round(self.distinct_genres, 4),
                "distinct_decades": round(self.distinct_decades, 4),
                "mean_top1_score": round(self.mean_top1_score, 4),
                "thin_evidence_rate": round(self.thin_evidence_rate, 4),
            },
            "popularity_diagnostics": {
                "popularity_spearman": round(self.popularity_spearman, 4),
                "mean_recommendation_popularity_pct": round(
                    self.mean_recommendation_popularity_pct, 3
                ),
            },
        }


def ndcg_at_k(gains: Sequence[float], k: int) -> float:
    """Binary-gain NDCG with a single relevance level."""
    if k <= 0:
        return 0.0
    dcg = sum(g / math.log2(i + 2) for i, g in enumerate(gains[:k]) if g > 0)
    ideal = sum(1.0 / math.log2(i + 2) for i in range(min(k, sum(1 for g in gains if g > 0))))
    return dcg / ideal if ideal > 0 else 0.0


def spearman(x: Sequence[float], y: Sequence[float]) -> float:
    """
    Spearman rank correlation, tie-corrected.

    Returns 0.0 for a constant input rather than NaN, so a diagnostic can
    never silently vanish from a report.
    """
    if len(x) != len(y) or len(x) < 2:
        return 0.0
    rx = _rankdata(x)
    ry = _rankdata(y)
    mx, my = rx.mean(), ry.mean()
    num = float(((rx - mx) * (ry - my)).sum())
    den = math.sqrt(float(((rx - mx) ** 2).sum()) * float(((ry - my) ** 2).sum()))
    return num / den if den > 0 else 0.0


def _rankdata(values: Sequence[float]) -> np.ndarray:
    """Average ranks, so tied scores do not inflate the correlation."""
    arr = np.asarray(values, dtype=np.float64)
    order = np.argsort(arr, kind="mergesort")
    ranks = np.empty(len(arr), dtype=np.float64)
    ranks[order] = np.arange(1, len(arr) + 1, dtype=np.float64)
    # Average ranks within tied groups.
    sorted_arr = arr[order]
    start = 0
    for i in range(1, len(arr) + 1):
        if i == len(arr) or sorted_arr[i] != sorted_arr[start]:
            if i - start > 1:
                ranks[order[start:i]] = (start + i + 1) / 2.0
            start = i
    return ranks


def evaluate_recommender(
    recommender,
    labels: ProxyLabels,
    *,
    name: str = "",
    k: int = 10,
    ks: Iterable[int] = DEFAULT_KS,
    query_ids: Sequence[int] | None = None,
    popularity_percentiles: Mapping[int, float] | None = None,
    thin_evidence_terms: int = THIN_EVIDENCE_TERMS,
    min_shared_terms: int = 1,
) -> EvaluationResult:
    """
    Leave-one-out evaluation over the whole catalogue.

    Every film is a query; its own id is excluded from its own list. There is
    no train/test split because there is nothing to train -- see the report's
    sec 2.5 -- so the protocol is transductive full-catalogue retrieval and the
    only honest unit of evaluation is the individual film.

    ``popularity_percentiles`` maps id -> percentile in [0, 100]. Computed once
    by the caller so every configuration is scored against the same scale.

    ``min_shared_terms`` rejects candidates sharing fewer than N non-zero terms
    with the query. It shortens lists, so ``mean_recommendations_returned`` is
    reported next to every metric: a filtered configuration is trading result
    count for evidence quality, and that trade must stay visible.
    """
    ks = tuple(sorted({k, *ks}))
    catalogue = recommender.movie_ids
    if query_ids is None:
        query_ids = [int(i) for i in catalogue]
    query_ids = [int(q) for q in query_ids]

    result = EvaluationResult(
        name=name, k=k, queries=len(query_ids), catalogue=len(catalogue)
    )

    # Relevant sets first, so unevaluable queries are counted honestly rather
    # than contributing a 0 that would look like a failure.
    relevant_by_label: dict[str, dict[int, frozenset[int]]] = {}
    for kind in LABEL_KINDS:
        table = {}
        for q in query_ids:
            group = labels.group_for(kind, q)
            if group:
                table[q] = group
        relevant_by_label[kind] = table
        result.unevaluable_queries[kind] = len(query_ids) - len(table)

    ids = recommender.movie_ids
    matrix = recommender.matrix
    corpus = recommender.corpus

    # Retrieval, batched so the dense block stays bounded.
    #
    # When an evidence filter is active the candidate pool is fetched DEEPER
    # than the cutoffs and truncated only after filtering. Filtering a pool that
    # was already cut to k cannot backfill: it can only delete, so it can never
    # promote a passing candidate that sat just below the cutoff. That made
    # label recall identical across every threshold in the first Phase 4 run,
    # which was an artifact of the truncation order, not a finding.
    pool = max(ks) if min_shared_terms <= 1 else min(len(catalogue), max(ks) * EVIDENCE_POOL_FACTOR)
    lists: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    step = 256
    for start in range(0, len(query_ids), step):
        block = query_ids[start : start + step]
        rows = recommender.rows_of(block)
        for movie_id, (idxs, scores) in zip(
            block, recommender.top_k_batch(rows, pool)
        ):
            lists[int(movie_id)] = (idxs, scores)

    top1_scores: list[float] = []
    popularity_pairs: list[tuple[float, float]] = []
    rec_popularity: list[float] = []
    thin_hits = 0
    thin_total = 0
    ild_terms: list[float] = []
    genre_breadth: list[float] = []
    decade_breadth: list[float] = []
    returned: list[int] = []
    seen_any: set[int] = set()

    for q in query_ids:
        idxs, scores = lists[q]
        idxs = [i for i in idxs.tolist() if int(ids[i]) != q][: max(ks)]
        query_nz = matrix[recommender.row_of(q)].indices

        # Shared non-zero terms, computed once per candidate and reused by the
        # evidence filter, the thin-evidence rate and the diagnostics.
        shared_counts = [
            int(np.intersect1d(query_nz, matrix[i].indices, assume_unique=True).size)
            for i in idxs
        ]
        if min_shared_terms > 1:
            kept = [
                (i, s, sh)
                for i, s, sh in zip(idxs, scores.tolist(), shared_counts)
                if sh >= min_shared_terms
            ]
            idxs = [i for i, _, _ in kept]
            kept_scores = [s for _, s, _ in kept]
            shared_counts = [sh for _, _, sh in kept]
        else:
            kept_scores = scores.tolist()

        returned.append(len(idxs))
        if kept_scores:
            top1_scores.append(float(kept_scores[0]))
        sub = matrix[idxs]
        for local, i in enumerate(idxs):
            seen_any.add(int(ids[i]))
            if popularity_percentiles is not None:
                pct = popularity_percentiles[int(ids[i])]
                rec_popularity.append(pct)
                popularity_pairs.append((float(local + 1), pct))
            if local < k:
                thin_total += 1
                if shared_counts[local] <= thin_evidence_terms:
                    thin_hits += 1
        if len(idxs) >= 2:
            gram = np.asarray((sub @ sub.T).todense(), dtype=np.float64)
            iu = np.triu_indices(gram.shape[0], k=1)
            ild_terms.extend((1.0 - gram[iu]).tolist())
        genre_breadth.append(
            len(
                {
                    g
                    for i in idxs[:k]
                    for g in corpus[i]["tokens"].get("genres", [])
                }
            )
        )
        decade_breadth.append(
            len(
                {
                    (corpus[i].get("release_year") or 0) // 10 * 10
                    for i in idxs[:k]
                    if corpus[i].get("release_year")
                }
            )
        )

    result.coverage = len(seen_any) / len(catalogue) if len(catalogue) else 0.0
    result.intra_list_diversity = float(np.mean(ild_terms)) if ild_terms else 0.0
    result.distinct_genres = float(np.mean(genre_breadth)) if genre_breadth else 0.0
    result.distinct_decades = float(np.mean(decade_breadth)) if decade_breadth else 0.0
    result.mean_top1_score = float(np.mean(top1_scores)) if top1_scores else 0.0
    result.thin_evidence_rate = thin_hits / thin_total if thin_total else 0.0
    result.mean_recommendations_returned = float(np.mean(returned)) if returned else 0.0
    if rec_popularity:
        result.mean_recommendation_popularity_pct = float(np.mean(rec_popularity))
        result.popularity_spearman = spearman(
            [p for p, _ in popularity_pairs], [v for _, v in popularity_pairs]
        )

    # Label scores, per label and per K, over that label's evaluable queries.
    for kind in LABEL_KINDS:
        table = relevant_by_label[kind]
        per_k: list[LabelScores] = []
        for cutoff in ks:
            recalls: list[float] = []
            hits = 0
            ndcgs: list[float] = []
            sizes: list[int] = []
            for q, relevant in table.items():
                idxs, _ = lists[q]
                got = [int(ids[i]) for i in idxs.tolist() if int(ids[i]) != q][:cutoff]
                found = len(set(got) & set(relevant))
                recalls.append(found / len(relevant))
                hits += found > 0
                ndcgs.append(ndcg_at_k([1.0 if int(ids[i]) in relevant else 0.0 for i in idxs[:cutoff]], cutoff))
                sizes.append(len(relevant))
            if not recalls:
                per_k.append(LabelScores(kind, cutoff, 0, 0.0, 0.0, 0.0, 0.0))
                continue
            per_k.append(
                LabelScores(
                    kind=kind,
                    k=cutoff,
                    queries_evaluated=len(recalls),
                    recall=float(np.mean(recalls)),
                    hit_rate=hits / len(recalls),
                    ndcg=float(np.mean(ndcgs)),
                    mean_relevant=float(np.mean(sizes)),
                )
            )
        result.label_scores[kind] = per_k
    return result
