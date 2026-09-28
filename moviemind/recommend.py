"""
Content-based retrieval over the Phase 3 representation.

**Scope guard.** This module retrieves and ranks by cosine similarity. It does
not train, fit or learn anything: no parameter is estimated from TMDb content,
and nothing here is a predictive model. Weights arrive already-set from
``PreprocessConfig``. That distinction is a licensing requirement, not a
stylistic one -- see docs/phase-02-dataset-audit.md sec 15.

Retrieval is sparse throughout. A single query costs one sparse mat-vec
(``X @ q``), never an ``N x N`` matrix. Batched evaluation bounds its dense
working set by ``batch_size x N`` and is opt-in; nothing in this module ever
materialises 5,000 x 5,000.

**Filtering contract.** ``min_score`` and ``min_shared_terms`` reject
candidates, so the retrieval they are applied to must be able to *promote* as
well as delete: :meth:`Recommender.recommend` ranks the whole candidate list and
scans it until it holds ``k`` accepted results or runs out, which is what lets a
rejection at rank 3 be backfilled from rank 11. Filtering a pre-truncated top-k
is the bug this design exists to prevent.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import scipy.sparse as sp

from .config import PreprocessConfig
from .representations import (
    REPRESENTATIONS,
    RepresentationResult,
    RepresentationSpec,
    build_representation,
)

#: Retrieval is cosine, so a blockwise L2-normalised TF-IDF means the score is
#: a plain dot product. No second similarity computation is needed.
SIMILARITY = "cosine (dot product over L2-normalised TF-IDF)"

#: Exposed so callers can record exactly what was measured.
SIMILARITY_METHOD = "cosine"


class UnknownMovieError(KeyError):
    """Raised when a TMDb id is not in the catalogue."""


class InsufficientFeaturesError(ValueError):
    """Raised when a film has no usable feature vector to search with."""


@dataclass(frozen=True)
class Recommendation:
    """One ranked result."""

    rank: int
    movie_id: int
    title: str | None
    score: float
    year: int | None
    genres: tuple[str, ...]
    popularity: float | None
    vote_average: float | None
    vote_count: int | None
    collection_name: str | None
    #: Number of non-zero terms the query and this film share. A score alone
    #: is not evidence of similarity: see ``thin_evidence`` in the report.
    shared_terms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "movie_id": self.movie_id,
            "title": self.title,
            "score": round(self.score, 6),
            "year": self.year,
            "genres": list(self.genres),
            "popularity": self.popularity,
            "vote_average": self.vote_average,
            "vote_count": self.vote_count,
            "collection_name": self.collection_name,
            "shared_terms": self.shared_terms,
        }


@dataclass(frozen=True)
class RecommendationResult:
    query_id: int
    query_title: str | None
    recommendations: tuple[Recommendation, ...]
    #: Films dropped before ranking, with the reason. Surfaced rather than
    #: silently swallowed, because a film with no features is a data condition
    #: the product has to explain, not a bug.
    skipped: tuple[tuple[int, str], ...] = ()

    def __len__(self) -> int:
        return len(self.recommendations)

    def __iter__(self):
        return iter(self.recommendations)

    @property
    def movie_ids(self) -> tuple[int, ...]:
        return tuple(r.movie_id for r in self.recommendations)

    def as_dict(self) -> dict[str, Any]:
        return {
            "query_id": self.query_id,
            "query_title": self.query_title,
            "similarity": SIMILARITY,
            "recommendations": [r.as_dict() for r in self.recommendations],
            "skipped": [{"movie_id": m, "reason": r} for m, r in self.skipped],
        }


def _sort_key(item: tuple[int, float, int]) -> tuple[float, int]:
    """
    Deterministic ranking: score descending, then **TMDb id ascending**.

    The tie-break is not cosmetic. Measured on representation A, 36.7% of the
    scores in the top-50 candidate pool are bit-identical duplicates (films
    matching on a handful of shared structural terms), so an unstable sort would
    emit a different top-K on a different machine or NumPy build. Sorting on the
    id makes the output a pure function of (matrix, k).
    """
    _idx, score, movie_id = item
    return (-score, movie_id)


def _positive_scores(
    matrix: sp.csr_matrix,
    query_row: int,
    *,
    exclude: int,
    candidates: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Every positively-scoring row against ``query_row``, unordered.

    Returns ``(indices, values)`` aligned to each other. The query row and every
    zero-scoring row are already removed, so callers never have to re-filter.
    Operates only on the query row's non-zeros, so cost is ``O(nnz(query))``
    rather than ``O(N)``.

    Both retrieval paths funnel through here so they can never disagree about
    which candidates exist or what they score.
    """
    # Both operands are sparse, so scipy returns a sparse product. `.todense()`
    # is required: the result is a single row vector, so this materialises
    # N floats, not an N x N matrix.
    scores = np.asarray((matrix @ matrix[query_row].T).todense()).ravel()
    nonzero = np.flatnonzero(scores)
    nonzero = nonzero[nonzero != exclude]
    if candidates is not None:
        nonzero = np.intersect1d(nonzero, candidates, assume_unique=False)
    if nonzero.size == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64)
    return nonzero, scores[nonzero]


def ranked_candidates(
    matrix: sp.csr_matrix,
    query_row: int,
    *,
    exclude: int,
    candidates: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    All positively-scoring candidates in deterministic rank order.

    Returns ``(indices, scores)`` sorted by descending score then ascending
    index, using the same total order as :func:`sparse_top_k`. Callers that need
    to *filter* must use this rather than ``sparse_top_k``: a filter applied to
    a pre-truncated top-k can only delete, never promote, so it silently returns
    short lists.

    This sorts the whole candidate set (``O(n log n)``) where ``sparse_top_k``
    selects in ``O(n)``. That is the deliberate trade: one total order over one
    pass is what makes the filtered result deterministic. An expanding-window
    scan would be cheaper in the unfiltered case, but with 36.7% of scores
    bit-identical, window boundaries fall inside tie groups and the accepted
    order stops being a pure function of (matrix, k).
    """
    nonzero, values = _positive_scores(
        matrix, query_row, exclude=exclude, candidates=candidates
    )
    if nonzero.size == 0:
        return nonzero, values
    order = np.lexsort((nonzero, -values))
    return nonzero[order], values[order]


def sparse_top_k(
    matrix: sp.csr_matrix,
    query_row: int,
    k: int,
    *,
    exclude: int,
    candidates: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray]:
    """
    Top-k rows most similar to ``query_row`` by dot product.

    Returns ``(indices, scores)`` sorted by descending score then ascending
    index. Operates only on the query row's non-zeros, so cost is
    ``O(nnz(query))`` rather than ``O(N)``.

    Use this when you want exactly the k best matches and will not filter them.
    Use :func:`ranked_candidates` when a filter may reject some of them.
    """
    nonzero, values = _positive_scores(
        matrix, query_row, exclude=exclude, candidates=candidates
    )
    if nonzero.size == 0:
        return np.empty(0, dtype=np.int64), np.empty(0, dtype=np.float64)
    if nonzero.size > k:
        # Partial sort: O(n) selection, then order the winners.
        top = np.argpartition(-values, k - 1)[:k]
        nonzero = nonzero[top]
        values = values[top]
    order = np.lexsort((nonzero, -values))
    return nonzero[order], values[order]


class Recommender:
    """
    Immutable, reusable index over one representation.

    Build once, query many times. The matrix is L2-normalised, so cosine needs
    no runtime work.
    """

    def __init__(
        self,
        corpus: Sequence[Mapping[str, Any]],
        result: RepresentationResult,
        *,
        config_fingerprint: str = "",
        min_score: float = 0.0,
        min_shared_terms: int = 1,
    ) -> None:
        """
        ``min_score`` / ``min_shared_terms`` reject weak evidence.

        Defaults keep every positive-scoring candidate. Raising
        ``min_shared_terms`` is the only available defence against
        thin-evidence matches: measured, 49.3% of representation D's top-10
        results rest on 3 or fewer shared non-zero terms, while top-1 cosines
        are low across the board (median 0.281) and unreliable at the top --
        9.0% of queries have a top-1 match sharing exactly one term, and those
        pairs reach cosine 1.00. An absolute score threshold therefore cannot
        separate a well-evidenced match from a one-term coincidence; a count of
        shared terms can.
        """
        if min_shared_terms < 1:
            raise ValueError("min_shared_terms must be >= 1")
        self._corpus = list(corpus)
        self._result = result
        self._config_fingerprint = config_fingerprint
        self._matrix: sp.csr_matrix = result.matrix.tocsr()
        self._ids = np.array([int(item["id"]) for item in self._corpus], dtype=np.int64)
        self._index = {int(movie_id): i for i, movie_id in enumerate(self._ids)}
        if len(self._index) != len(self._ids):
            raise ValueError("corpus contains duplicate movie ids")
        self._min_score = float(min_score)
        self._min_shared_terms = int(min_shared_terms)

    # -- construction ---------------------------------------------------
    @classmethod
    def build(
        cls,
        corpus: Sequence[Mapping[str, Any]],
        config: PreprocessConfig,
        *,
        representation: str | RepresentationSpec = "D",
        **kwargs: Any,
    ) -> Recommender:
        """
        Vectorise ``corpus`` under ``config`` and wrap it in a Recommender.

        ``representation`` is either one of the Phase 3 keys (A-D) or a custom
        ``RepresentationSpec``, which is how the ablation and single-field
        experiments in moviemind.experiments build non-catalogued field sets.
        """
        spec: RepresentationSpec | None
        if isinstance(representation, RepresentationSpec):
            spec = representation
        else:
            spec = next((s for s in REPRESENTATIONS if s.key == representation), None)
            if spec is None:
                raise ValueError(
                    f"unknown representation {representation!r}; "
                    f"use one of {', '.join(s.key for s in REPRESENTATIONS)} "
                    "or pass a RepresentationSpec"
                )
        result = build_representation(corpus, spec, config)
        return cls(corpus, result, config_fingerprint=config.fingerprint(), **kwargs)

    @classmethod
    def from_corpus_file(
        cls,
        path: str | Path,
        config: PreprocessConfig,
        **kwargs: Any,
    ) -> Recommender:
        return cls.build(load_corpus(path), config, **kwargs)

    # -- introspection ---------------------------------------------------
    @property
    def size(self) -> int:
        return len(self._corpus)

    @property
    def dimensions(self) -> int:
        return int(self._matrix.shape[1])

    @property
    def nonzeros(self) -> int:
        return int(self._matrix.nnz)

    @property
    def density_percent(self) -> float:
        return self._result.density_percent

    @property
    def movie_ids(self) -> np.ndarray:
        return self._ids.copy()

    @property
    def config_fingerprint(self) -> str:
        return self._config_fingerprint

    @property
    def field_contribution_share(self) -> dict[str, float]:
        return self._result.field_contribution_share()

    @property
    def feature_names(self) -> list[str]:
        return list(self._result.feature_names)

    def has(self, movie_id: int) -> bool:
        return int(movie_id) in self._index

    def row_of(self, movie_id: int) -> int:
        """Row position of a film in the matrix. Public for offline scoring."""
        return self._require_index(movie_id)

    def rows_of(self, movie_ids: Iterable[int]) -> np.ndarray:
        return np.array(
            [self._require_index(int(m)) for m in movie_ids], dtype=np.int64
        )

    @property
    def corpus(self) -> list[Mapping[str, Any]]:
        """The indexed feature records, row-aligned with the matrix."""
        return self._corpus

    @property
    def matrix(self) -> sp.csr_matrix:
        return self._matrix

    def metadata(self, movie_id: int) -> dict[str, Any]:
        if int(movie_id) not in self._index:
            raise UnknownMovieError(
                f"tmdb id {movie_id} is not in the catalogue "
                f"({len(self._corpus)} films indexed)"
            )
        item = self._corpus[self._index[int(movie_id)]]
        return {
            "movie_id": int(item["id"]),
            "title": item.get("title"),
            "original_title": item.get("original_title"),
            "release_year": item.get("release_year"),
            "genres": sorted(
                t.split(":", 1)[1].replace("_", " ")
                for t in item["tokens"].get("genres", [])
            ),
            "collection_name": item.get("collection_name"),
            "popularity": item.get("popularity"),
            "vote_average": item.get("vote_average"),
            "vote_count": item.get("vote_count"),
            "feature_counts": {k: len(v) for k, v in sorted(item["tokens"].items())},
            "document_size": item.get("document_size"),
        }

    def has_usable_features(self, movie_id: int) -> bool:
        """False when the film vector is all-zero and cannot match anything."""
        return self.row_nonzeros(self._require_index(movie_id)) > 0

    def row_nonzeros(self, row: int) -> int:
        return int(self._matrix.indptr[row + 1] - self._matrix.indptr[row])

    # -- retrieval --------------------------------------------------------
    def _require_index(self, movie_id: int) -> int:
        try:
            return self._index[int(movie_id)]
        except KeyError:
            raise UnknownMovieError(
                f"tmdb id {movie_id} is not in the catalogue "
                f"({len(self._corpus)} films indexed)"
            ) from None

    def recommend(self, movie_id: int, k: int = 10) -> RecommendationResult:
        """
        Up to ``k`` recommendations for a canonical TMDb id.

        **Backfill contract.** The evidence filter (``min_score`` /
        ``min_shared_terms``) rejects candidates, so the accepted set is drawn
        from the whole ranked candidate list rather than from a pre-truncated
        top-k. Scanning continues until ``k`` valid recommendations have been
        collected or the candidate pool is exhausted, which means:

        * a rejection at rank 3 is backfilled by a passing film at rank 11;
        * the result holds exactly ``k`` items whenever the catalogue contains
          ``k`` valid candidates for the query;
        * it holds fewer than ``k`` only when the catalogue genuinely lacks
          enough valid candidates, and never more than ``k``.

        Ranking order is preserved among accepted candidates: they are emitted
        in the same descending-score, ascending-id order the unfiltered query
        would have produced, so the filter only ever removes rows from that
        sequence, never reorders it.

        The query film is always excluded, and every returned id is distinct --
        the candidate list is a set of unique matrix rows, and the constructor
        already rejects a corpus containing duplicate ids.

        Raises ``UnknownMovieError`` for an id that is not in the catalogue and
        ``InsufficientFeaturesError`` for a film with an all-zero vector -- both
        are caller errors, distinguishable from the data conditions reported in
        ``skipped``.
        """
        if k < 1:
            raise ValueError(f"k must be >= 1, got {k}")
        row = self._require_index(movie_id)
        if self.row_nonzeros(row) == 0:
            meta = self._corpus[row]
            raise InsufficientFeaturesError(
                f"tmdb id {movie_id} ({meta.get('title')!r}) has an all-zero feature "
                "vector: it has no overview, keywords, genres, cast or director "
                "tokens, so no content-based neighbour exists"
            )

        # A membership mask over the feature space turns the shared-term count
        # into a gather-and-sum with no per-candidate sort. `np.intersect1d`
        # would re-sort both operands for every candidate examined, and the
        # backfill scan below examines far more candidates than a plain top-k
        # did, so that cost is not affordable here. Allocated per call rather
        # than cached on the instance so concurrent queries cannot race.
        #
        # The count is always computed, even when no threshold is active,
        # because `shared_terms` is reported on every result and is the
        # evidence a caller needs to judge a recommendation.
        indptr, data = self._matrix.indptr, self._matrix.indices
        query_terms = np.zeros(self.dimensions, dtype=bool)
        query_terms[data[indptr[row] : indptr[row + 1]]] = True

        # The full ranked candidate list, not a top-k slice: the filter below
        # must be able to promote a passing candidate from below the cutoff.
        indices, scores = ranked_candidates(self._matrix, row, exclude=row)

        recommendations: list[Recommendation] = []
        skipped: list[tuple[int, str]] = []
        for idx, score in zip(indices.tolist(), scores.tolist()):
            candidate = self._corpus[idx]
            if score < self._min_score:
                skipped.append((int(candidate["id"]), "below_min_score"))
                continue
            # Read the candidate's terms straight out of the CSR arrays.
            # Indexing the matrix (`self._matrix[idx]`) would build a fresh
            # 1 x D sparse object per candidate, which dominates the cost of a
            # scan that now visits hundreds of rows instead of ten.
            shared = int(np.count_nonzero(query_terms[data[indptr[idx] : indptr[idx + 1]]]))
            if shared < self._min_shared_terms:
                skipped.append((int(candidate["id"]), "insufficient_shared_terms"))
                continue
            recommendations.append(
                Recommendation(
                    rank=len(recommendations) + 1,
                    movie_id=int(candidate["id"]),
                    title=candidate.get("title"),
                    score=float(score),
                    year=candidate.get("release_year"),
                    genres=tuple(
                        sorted(
                            t.split(":", 1)[1].replace("_", " ")
                            for t in candidate["tokens"].get("genres", [])
                        )
                    ),
                    popularity=candidate.get("popularity"),
                    vote_average=candidate.get("vote_average"),
                    vote_count=candidate.get("vote_count"),
                    collection_name=candidate.get("collection_name"),
                    shared_terms=shared,
                )
            )
            # Stop at k. Everything ranked below this point is neither returned
            # nor reported as skipped, so `skipped` describes only the candidates
            # actually considered -- which is what makes it a usable diagnostic
            # rather than a full-catalogue dump.
            if len(recommendations) >= k:
                break

        return RecommendationResult(
            query_id=int(movie_id),
            query_title=self._corpus[row].get("title"),
            recommendations=tuple(recommendations),
            skipped=tuple(skipped),
        )

    def recommend_many(
        self, movie_ids: Iterable[int], k: int = 10
    ) -> dict[int, RecommendationResult]:
        """Convenience for batch use. Unknown ids raise, as in ``recommend``."""
        return {int(m): self.recommend(m, k) for m in movie_ids}

    # -- evaluation support ----------------------------------------------
    def similarity_rows(self, rows: np.ndarray) -> sp.csr_matrix:
        """
        ``X[rows] @ X.T`` as sparse, for batched offline scoring.

        The dense equivalent of this call is ``len(rows) x N``, so callers must
        keep ``rows`` bounded. This is the only place a wide product is formed
        and it is never square unless the caller asks for it.
        """
        rows = np.asarray(rows, dtype=np.int64)
        return sp.csr_matrix(self._matrix[rows] @ self._matrix.T)

    def top_k_batch(
        self, rows: np.ndarray, k: int = 10, *, candidates: np.ndarray | None = None
    ) -> list[tuple[np.ndarray, np.ndarray]]:
        """
        Batched top-k. Returns one ``(indices, scores)`` pair per input row.

        Scores are computed densely per batch (``batch x N``) for speed, then
        reduced to the top-k immediately so only ``batch x k`` is retained.
        """
        rows = np.asarray(rows, dtype=np.int64)
        if candidates is not None:
            candidates = np.asarray(candidates, dtype=np.int64)
        out: list[tuple[np.ndarray, np.ndarray]] = []
        # Bounded so the dense block stays small: 256 x 5000 float32 is 5 MB.
        step = max(1, min(len(rows), (1 << 22) // max(len(self._ids), 1)))
        for start in range(0, len(rows), step):
            block = rows[start : start + step]
            sims = np.asarray((self._matrix[block] @ self._matrix.T).todense())
            for local, row in enumerate(block):
                sims[local, row] = -np.inf          # never recommend the query
                if candidates is not None:
                    mask = np.full(sims.shape[1], -np.inf, dtype=np.float32)
                    mask[candidates] = 0.0
                    sims[local] += mask
                n = min(k, sims.shape[1])
                top = np.argpartition(-sims[local], n - 1)[:n]
                top = top[np.lexsort((top, -sims[local][top]))]
                out.append((top, sims[local][top].astype(np.float64)))
        return out


def load_corpus(path: str | Path) -> list[dict[str, Any]]:
    """Read a Phase 3 processed corpus."""
    file = Path(path)
    if not file.exists():
        raise SystemExit(
            f"ERROR: processed corpus not found at {file}\n"
            "       run: python scripts/build_features.py"
        )
    records = []
    with file.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records
