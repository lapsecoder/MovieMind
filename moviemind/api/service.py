"""
Service layer: the single boundary between HTTP and the Phase 4 engine.

Routes call this; this calls :class:`~moviemind.recommend.Recommender`. No route
in :mod:`moviemind.api.app` reaches into the engine directly, and this module
imports nothing from FastAPI. That split is the whole point of the layer: the
engine stays testable without an app, and the API stays a thin translation of
HTTP onto engine calls that already existed and were already measured.

**The configuration is imported, never restated.** ``SELECTED_GENRE_WEIGHT`` and
``SELECTED_MIN_SHARED_TERMS`` come from :mod:`moviemind.experiments`, the same
constants the Phase 4 report and the experiment runner use. A literal ``0.44``
typed here would be a second source of truth that could drift from the measured
one without anything failing.
"""

from __future__ import annotations

import logging
import time
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import replace
from pathlib import Path
from typing import Any

from ..config import PreprocessConfig
from ..experiments import SELECTED_GENRE_WEIGHT, SELECTED_MIN_SHARED_TERMS
from ..recommend import (
    InsufficientFeaturesError as EngineInsufficientFeatures,
)
from ..recommend import (
    Recommender,
    load_corpus,
)
from ..recommend import (
    UnknownMovieError as EngineUnknownMovie,
)
from . import schemas
from .errors import (
    EmptyQueryError,
    InsufficientFeaturesError,
    InvalidKError,
    InvalidLimitError,
    QueryTooLongError,
    UnknownMovieError,
)
from .settings import Settings

logger = logging.getLogger(__name__)

#: Representation key selected in Phase 4. See docs/phase-04-... sec 7-8.
REPRESENTATION_KEY = "D"

#: Verbatim attribution required by TMDb's terms, with the bracketed placeholder
#: resolved to "product". Served by the API so a frontend can render it without
#: retyping it -- a paraphrase would not satisfy the licence. Wording, including
#: the comma after "certified", is fixed by docs/phase-01-dataset-strategy.md
#: sec 7.1 and docs/phase-02-dataset-audit.md; do not reword it casually.
TMDB_NOTICE = (
    "This product uses TMDB and the TMDB APIs but is not endorsed, certified, "
    "or otherwise approved by TMDB."
)

TMDB_LICENSE_SUMMARY = (
    "TMDb data is free for non-commercial use with mandatory attribution; "
    "commercial use requires a separate written agreement. The snapshot is not "
    "redistributable. Not legal advice - see docs/phase-02-dataset-audit.md."
)

#: Carried into /api/v1/meta so a client can surface the honest caveats instead
#: of implying the numbers are stronger than they are.
META_NOTES = (
    "Content-based retrieval only. No model is trained and no user data is "
    "collected; there are no accounts and nothing is persisted.",
    "There is no relevance ground truth in this dataset. Every Phase 4 score is "
    "agreement with a structural proxy, not user satisfaction.",
    "min_shared_terms=3 leaves a measured thin-evidence rate of 49.3% at the top "
    "10; a returned film is a defensible match, not a verified one.",
    "Cast is diluted inside representation D (recall@10 0.157). A user searching "
    "by actor is poorly served, which is why search covers titles only.",
    "A result list shorter than k means the catalogue held too few candidates at "
    "the evidence threshold, not that the engine stopped searching.",
)


class CatalogueLoadError(RuntimeError):
    """Raised at startup when the recommendation index cannot be built."""


#: The Phase 4 engine emits exactly these two skip reasons (moviemind/recommend.py
#: appends them as literals). The response schema pins them as a ``Literal``, and
#: this table is the narrowing step between the engine's ``tuple[int, str]`` and
#: that ``Literal``.
#:
#: A miss here is a genuine contract break -- the engine gained a skip reason and
#: the API's evidence vocabulary did not follow -- so it raises rather than
#: relabelling the reason. Quietly mapping an unknown reason to a known one would
#: misreport *why* a film was dropped, which is the one thing the ``skipped``
#: block exists to tell a client.
SKIP_REASONS: dict[str, schemas.SkipReason] = {
    "below_min_score": "below_min_score",
    "insufficient_shared_terms": "insufficient_shared_terms",
}


def _normalise(value: object) -> str:
    """
    Fold a string for case- and accent-insensitive comparison.

    NFC then ``casefold``. ``casefold`` rather than ``lower`` because it is the
    Unicode-correct comparison: it maps ``ß`` to ``ss`` and the Turkish dotted
    capital I correctly, where ``lower`` does not. Accents are *not* stripped --
    Phase 3 sec 2.2 established that folding them destroys real distinctions
    ("Amelie" vs "Amelie"), and the same argument applies to search.
    """
    if not isinstance(value, str):
        return ""
    return unicodedata.normalize("NFC", value).casefold().strip()


def _genre_names(record: Mapping[str, Any]) -> list[str]:
    """
    Human genre names from the namespaced token list.

    Mirrors the parse in ``Recommender.metadata`` (strip the ``gn:`` namespace,
    turn underscores back into spaces). Kept as one local helper so search and
    details cannot drift apart on how a genre is spelled.
    """
    tokens: Iterable[str] = record.get("tokens", {}).get("genres", [])
    return sorted(
        token.split(":", 1)[1].replace("_", " ")
        for token in tokens
        if ":" in token
    )


class CatalogueService:
    """
    Read-only façade over one loaded :class:`Recommender`.

    Immutable after construction and safe to share across requests: the engine
    holds no per-query state, and the search index is built once. Nothing here
    mutates, so there is nothing to lock.
    """

    def __init__(
        self,
        recommender: Recommender,
        settings: Settings,
        *,
        load_seconds: float,
    ) -> None:
        self._recommender = recommender
        self._settings = settings
        self._load_seconds = load_seconds

        # Row-aligned view of the corpus, kept once. The engine already holds
        # this list; the dict is a second *index* into it (id -> position), not a
        # second copy of the records, so it costs ~0.4 MB for 5,000 films rather
        # than duplicating the parsed corpus.
        self._corpus = recommender.corpus
        self._by_id: dict[int, Mapping[str, Any]] = {
            int(record["id"]): record for record in self._corpus
        }

        # Pre-normalised search keys, built once so a request does no Unicode
        # work per candidate. A flat list beats an inverted index here: at 5,000
        # titles a linear scan is ~1 ms, and an index would add invalidation
        # logic and a second thing to keep correct for no measurable gain.
        self._search_keys: list[tuple[int, str, str, int, list[str], float | None]] = []
        for record in self._corpus:
            movie_id = int(record["id"])
            self._search_keys.append(
                (
                    movie_id,
                    _normalise(record.get("title")),
                    _normalise(record.get("original_title")),
                    int(record["release_year"]) if record.get("release_year") else 0,
                    _genre_names(record),
                    record.get("popularity"),
                )
            )

    # -- construction ----------------------------------------------------
    @classmethod
    def load(cls, settings: Settings | None = None) -> CatalogueService:
        """
        Build the service from the Phase 3 corpus at the locked Phase 4 config.

        Loads the corpus and vectorises it **once**, at startup. A request never
        rebuilds TF-IDF: the representation is a pure function of the corpus and
        the config, so recomputing it per request would be both wrong (a different
        vocabulary) and ruinous -- 0.27 s of startup work against a 1.0 ms query,
        roughly 270x, on every single call.

        Raises :class:`CatalogueLoadError` with an actionable message rather than
        letting a ``FileNotFoundError`` or a ``SystemExit`` from
        ``load_corpus`` escape into the ASGI layer.
        """
        settings = settings or Settings()
        started = time.perf_counter()

        path = Path(settings.corpus_path)
        if not path.exists():
            raise CatalogueLoadError(
                f"processed corpus not found at {path}. "
                "Build it first: python scripts/build_features.py"
            )

        try:
            corpus = load_corpus(path)
        except (OSError, ValueError) as exc:
            raise CatalogueLoadError(f"could not read {path}: {exc}") from exc

        if not corpus:
            raise CatalogueLoadError(f"{path} is empty; expected one JSON record per line")

        # The locked configuration. `dataclasses.replace` on the frozen Phase 3
        # config, so the API cannot drift from what Phase 4 measured.
        base = PreprocessConfig()
        config = replace(base, genres=replace(base.genres, weight=SELECTED_GENRE_WEIGHT))

        try:
            recommender = Recommender.build(
                corpus,
                config,
                representation=REPRESENTATION_KEY,
                min_shared_terms=SELECTED_MIN_SHARED_TERMS,
            )
        except (ValueError, KeyError) as exc:
            raise CatalogueLoadError(
                f"could not build the representation from {path}: {exc}"
            ) from exc

        elapsed = time.perf_counter() - started
        logger.info(
            "catalogue loaded: %d films, %d dimensions, %.1f ms",
            recommender.size,
            recommender.dimensions,
            elapsed * 1000,
        )
        return cls(recommender, settings, load_seconds=elapsed)

    # -- introspection ---------------------------------------------------
    @property
    def recommender(self) -> Recommender:
        return self._recommender

    @property
    def settings(self) -> Settings:
        return self._settings

    def stats(self) -> schemas.CatalogueStats:
        return schemas.CatalogueStats(
            films=self._recommender.size,
            dimensions=self._recommender.dimensions,
            nonzeros=self._recommender.nonzeros,
            density_percent=round(self._recommender.density_percent, 6),
            load_seconds=round(self._load_seconds, 4),
        )

    def meta(self, api_version: str) -> schemas.MetaResponse:
        return schemas.MetaResponse(
            api_version=api_version,
            representation=REPRESENTATION_KEY,
            config_fingerprint=self._recommender.config_fingerprint,
            min_shared_terms=SELECTED_MIN_SHARED_TERMS,
            similarity="cosine",
            limits=self._settings.describe_limits(),
            attribution=schemas.Attribution(
                notice=TMDB_NOTICE,
                source="themoviedb.org",
                license=TMDB_LICENSE_SUMMARY,
            ),
            notes=list(META_NOTES),
        )

    # -- validation helpers ----------------------------------------------
    def validate_k(self, k: int | None) -> int:
        """Resolve and range-check ``k`` against the configured ceiling."""
        value = self._settings.default_k if k is None else k
        if not 1 <= value <= self._settings.max_k:
            raise InvalidKError(
                details={
                    "k": value,
                    "min_k": 1,
                    "max_k": self._settings.max_k,
                    "default_k": self._settings.default_k,
                },
            )
        return value

    def validate_limit(self, limit: int | None) -> int:
        value = self._settings.default_search_limit if limit is None else limit
        if not 1 <= value <= self._settings.max_search_limit:
            raise InvalidLimitError(
                details={
                    "limit": value,
                    "min_limit": 1,
                    "max_limit": self._settings.max_search_limit,
                    "default_limit": self._settings.default_search_limit,
                },
            )
        return value

    def _require_known(self, movie_id: int) -> Mapping[str, Any]:
        record = self._by_id.get(int(movie_id))
        if record is None:
            raise UnknownMovieError(
                details={
                    "movie_id": int(movie_id),
                    "catalogue_size": len(self._by_id),
                },
            )
        return record

    # -- operations ------------------------------------------------------
    def search(self, query: str, limit: int | None = None) -> schemas.SearchResponse:
        """
        Case-insensitive substring search over film titles.

        Titles only, on purpose. The corpus also holds cast and director names,
        so searching them is easy -- but Phase 4 measured cast recall@10 at 0.157
        inside representation D (sec 5.4), which means an actor search would
        return films the recommender does not actually consider similar.
        Offering it would be a promise the engine does not keep. Genres have the
        same problem: 19 types, so a genre query is a filter, not a search.

        Ranking is by match tier, then normalised title, then TMDb id. The last
        key is what makes this deterministic: two films can normalise to the same
        string ("Amadeu" / "Amadeu"), and without it their relative order would
        depend on corpus position.
        """
        if not isinstance(query, str) or not query.strip():
            raise EmptyQueryError()
        if len(query) > self._settings.max_query_length:
            raise QueryTooLongError(
                details={"length": len(query), "max_query_length": self._settings.max_query_length}
            )

        resolved_limit = self.validate_limit(limit)
        needle = _normalise(query)

        # (tier, normalised title for ordering, id) -> the smallest tier wins,
        # then alphabetical, then id.
        scored: list[tuple[int, str, int, str, Mapping[str, Any], list[str], float | None]] = []
        for movie_id, title_key, original_key, _year, genres, popularity in self._search_keys:
            tier: int | None = None
            kind: str = ""
            if title_key == needle or original_key == needle:
                tier, kind = 0, "exact"
            elif title_key.startswith(needle):
                tier, kind = 1, "title_prefix"
            elif original_key.startswith(needle):
                tier, kind = 2, "original_title_prefix"
            elif needle in title_key:
                tier, kind = 3, "title_contains"
            elif needle in original_key:
                tier, kind = 4, "title_contains"
            if tier is None:
                continue
            # Sort on the *title* the query matched, so a prefix hit on the
            # original title does not order by an unrelated English title.
            order_key = title_key if kind != "original_title_prefix" else original_key
            scored.append((tier, order_key, movie_id, kind, self._by_id[movie_id], genres, popularity))

        scored.sort(key=lambda row: (row[0], row[1], row[2]))
        total = len(scored)

        hits = [
            schemas.SearchHit(
                movie_id=movie_id,
                title=record.get("title"),
                original_title=record.get("original_title"),
                release_year=(int(record["release_year"]) if record.get("release_year") else None),
                genres=genres,
                popularity=popularity,
                match=kind,  # type: ignore[arg-type]
            )
            for _tier, _order, movie_id, kind, record, genres, popularity in scored[:resolved_limit]
        ]

        return schemas.SearchResponse(
            query=query.strip(),
            limit=resolved_limit,
            total_matches=total,
            count=len(hits),
            results=hits,
        )

    def details(self, movie_id: int) -> schemas.MovieDetail:
        """Full metadata for one film. No token or document fields are exposed."""
        record = self._require_known(movie_id)
        engine_metadata = self._recommender.metadata(int(movie_id))
        return schemas.MovieDetail(
            movie_id=int(record["id"]),
            title=record.get("title"),
            original_title=record.get("original_title"),
            release_year=record.get("release_year"),
            release_date=record.get("release_date"),
            runtime_minutes=record.get("runtime_minutes"),
            original_language=record.get("original_language"),
            genres=_genre_names(record),
            collection_name=record.get("collection_name"),
            popularity=record.get("popularity"),
            vote_average=record.get("vote_average"),
            vote_count=record.get("vote_count"),
            adult=bool(record.get("adult", False)),
            # Term *counts* per field, from the engine's own view, so the
            # numbers agree with what was actually indexed.
            feature_counts=engine_metadata.get("feature_counts", {}),
            document_size=record.get("document_size"),
            # The one field the engine cannot supply directly without raising.
            recommendable=self._recommender.has_usable_features(int(movie_id)),
        )

    def recommend(self, movie_id: int, k: int | None = None) -> schemas.RecommendationResponse:
        """
        Delegate to the Phase 4 engine and re-shape its result.

        Every ordering, exclusion, filtering and backfill guarantee is the
        engine's, not this method's. The only judgement here is turning the
        engine's two exceptions into HTTP-shaped errors and computing the
        ``evidence`` block so a client can see the backfill contract hold.
        """
        resolved_k = self.validate_k(k)
        self._require_known(movie_id)

        try:
            result = self._recommender.recommend(int(movie_id), k=resolved_k)
        except EngineUnknownMovie as exc:  # pragma: no cover - guarded above
            raise UnknownMovieError(details={"movie_id": int(movie_id)}) from exc
        except EngineInsufficientFeatures as exc:
            raise InsufficientFeaturesError(
                details={
                    "movie_id": int(movie_id),
                    "reason": "all_zero_feature_vector",
                },
            ) from exc

        recommendations = [
            schemas.RecommendationItem(
                rank=item.rank,
                movie_id=item.movie_id,
                title=item.title,
                score=item.score,
                year=item.year,
                genres=list(item.genres),
                popularity=item.popularity,
                vote_average=item.vote_average,
                vote_count=item.vote_count,
                collection_name=item.collection_name,
                shared_terms=item.shared_terms,
            )
            for item in result.recommendations
        ]
        rejected = len(result.skipped)
        return schemas.RecommendationResponse(
            query=schemas.QueryMovie(
                movie_id=result.query_id,
                title=result.query_title,
            ),
            k=resolved_k,
            similarity="cosine",
            evidence=schemas.EvidenceInfo(
                min_shared_terms=SELECTED_MIN_SHARED_TERMS,
                requested_k=resolved_k,
                returned=len(recommendations),
                candidates_examined=len(recommendations) + rejected,
                candidates_rejected=rejected,
                exhausted=len(recommendations) < resolved_k,
            ),
            recommendations=recommendations,
            skipped=[
                schemas.SkippedCandidate(movie_id=movie_id, reason=SKIP_REASONS[reason])
                for movie_id, reason in result.skipped
            ],
        )


def build_service(corpus: Sequence[Mapping[str, Any]], settings: Settings) -> CatalogueService:
    """
    Build a service from an in-memory corpus, bypassing the filesystem.

    Used by the API tests so they can exercise the real engine against a small
    controlled catalogue without depending on ``data/processed/movies.jsonl``
    being present, and without any network access.
    """
    started = time.perf_counter()
    base = PreprocessConfig()
    config = replace(base, genres=replace(base.genres, weight=SELECTED_GENRE_WEIGHT))
    recommender = Recommender.build(
        list(corpus),
        config,
        representation=REPRESENTATION_KEY,
        min_shared_terms=SELECTED_MIN_SHARED_TERMS,
    )
    return CatalogueService(recommender, settings, load_seconds=time.perf_counter() - started)
