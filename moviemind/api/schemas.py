"""
Pydantic response models — the documented, validated shape of every response.

**These are a contract, not a convenience.** Adding a field is a minor change;
renaming or removing one breaks clients. Removing a field is therefore not done
silently: the engine's ``Recommendation.as_dict`` already fixes the shape of a
recommendation item, and these models mirror it exactly rather than reshaping
it, so the Phase 4 evidence fields (``score``, ``shared_terms``) reach the client
with their meaning intact.

Nothing internal leaks. The Phase 3 corpus record carries ``document`` (the full
token list) and ``tokens`` (per-field vocabulary); neither appears in any model
here. ``feature_counts`` -- the *number* of terms per field -- is exposed on
details, because it explains why a film matches weakly without revealing the
vocabulary the index was built from.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

#: How a search result matched, best tier first. Returned so a UI can explain a
#: hit ("exact title" reads very differently from "title contains") without
#: re-deriving it.
MatchKind = Literal["exact", "title_prefix", "original_title_prefix", "title_contains"]


class ErrorBody(BaseModel):
    code: str = Field(description="Stable machine-readable error code.")
    message: str = Field(description="Human-readable explanation. May be reworded.")
    status: int = Field(description="HTTP status code, repeated for convenience.")
    details: dict[str, Any] | None = Field(
        default=None, description="Optional structured context."
    )


class ErrorResponse(BaseModel):
    """The single error envelope. See moviemind/api/errors.py."""

    error: ErrorBody


class HealthResponse(BaseModel):
    """Liveness. Says the process is up; says nothing about the catalogue."""

    status: Literal["ok"] = "ok"
    service: str = "moviemind-api"
    api_version: str


class CatalogueStats(BaseModel):
    """What is actually loaded. Useful for a status page and for bug reports."""

    films: int = Field(description="Number of films indexed.")
    dimensions: int = Field(description="Feature-space width.")
    nonzeros: int = Field(description="Stored non-zero entries in the matrix.")
    density_percent: float
    load_seconds: float = Field(
        description="Wall-clock time spent loading and indexing at startup."
    )


class ReadyResponse(BaseModel):
    """Readiness. 200 when the index is loaded, 503 when it is not."""

    status: Literal["ready", "not_ready"]
    reason: str | None = Field(
        default=None,
        description="Why the service is not ready. Present only when not_ready.",
    )
    catalogue: CatalogueStats | None = None


class Attribution(BaseModel):
    """
    TMDb attribution, served by the API so a frontend can render it verbatim.

    Required by TMDb's terms (docs/phase-01-dataset-strategy.md sec 7.1) and
    deliberately machine-readable rather than buried in a docs page: the notice
    has to appear in the product's About/Credits area, and a frontend should be
    able to fetch the exact wording instead of retyping it.
    """

    notice: str = Field(
        description=(
            "Must be displayed prominently and verbatim in the product's "
            "About or Credits section."
        )
    )
    source: str = "themoviedb.org"
    license: str = Field(
        description="Summarised usage terms. Not legal advice; see the phase docs."
    )


class MetaResponse(BaseModel):
    """
    The locked Phase 4 configuration, as the API is actually running it.

    Exposed for three reasons: a client can show provenance instead of claiming
    a magic index; a mismatch against the documented fingerprint is immediately
    visible; and the attribution notice has to be reachable at runtime.
    """

    api_version: str
    representation: str
    config_fingerprint: str
    min_shared_terms: int
    similarity: str
    limits: dict[str, int]
    attribution: Attribution
    notes: list[str] = Field(
        default_factory=list,
        description="Caveats a client should surface, straight from the phase reports.",
    )


class SearchHit(BaseModel):
    movie_id: int
    title: str | None
    original_title: str | None
    release_year: int | None
    genres: list[str] = Field(default_factory=list)
    popularity: float | None = None
    match: MatchKind = Field(description="Which rule produced this hit.")


class SearchResponse(BaseModel):
    query: str = Field(description="The normalised query that was executed.")
    limit: int
    #: Total matches found *before* ``limit`` was applied. Lets a UI say
    #: "showing 10 of 214" instead of implying the catalogue holds ten.
    total_matches: int
    count: int = Field(description="Number of results returned, i.e. len(results).")
    results: list[SearchHit]


class MovieDetail(BaseModel):
    """A film's metadata. Deliberately excludes the token/document fields."""

    movie_id: int
    title: str | None
    original_title: str | None
    release_year: int | None
    release_date: str | None
    runtime_minutes: int | None
    original_language: str | None
    genres: list[str] = Field(default_factory=list)
    collection_name: str | None
    popularity: float | None
    vote_average: float | None
    vote_count: int | None
    adult: bool = False
    feature_counts: dict[str, int] = Field(
        default_factory=dict,
        description="Number of indexed terms per field. Counts only, never the terms.",
    )
    document_size: int | None = Field(
        default=None, description="Total indexed terms across all fields."
    )
    #: False when the film has an all-zero vector and therefore cannot be
    #: recommended *for*. Lets a UI disable the "show similar" affordance
    #: instead of letting the user hit a 422.
    recommendable: bool = True


class RecommendationItem(BaseModel):
    """One result. Field-for-field identical to the engine's own output."""

    rank: int
    movie_id: int
    title: str | None
    score: float = Field(description="Cosine similarity in [0, 1]; higher is closer.")
    year: int | None
    genres: list[str] = Field(default_factory=list)
    popularity: float | None
    vote_average: float | None
    vote_count: int | None
    collection_name: str | None
    shared_terms: int = Field(
        description=(
            "Non-zero terms shared with the query. The evidence behind the score: "
            "see docs/phase-04-recommendation-evaluation.md sec 5.6."
        )
    )


#: The Phase 4 engine's skip vocabulary (moviemind/recommend.py). Exported as an
#: alias so the schema and the service's narrowing table cannot disagree.
SkipReason = Literal["below_min_score", "insufficient_shared_terms"]


class SkippedCandidate(BaseModel):
    movie_id: int
    reason: SkipReason


class QueryMovie(BaseModel):
    movie_id: int
    title: str | None


class EvidenceInfo(BaseModel):
    """
    What the engine filtered on, and how hard it had to work.

    ``min_shared_terms`` is published because it changes what a result *means*:
    at 3, a returned film genuinely shares three indexed terms, and a client
    rendering "because you liked X" is making a defensible claim. ``returned``
    versus ``requested`` makes the backfill contract observable — a short list
    now means the catalogue ran out, not that the engine stopped looking.
    """

    min_shared_terms: int
    requested_k: int
    returned: int
    candidates_examined: int = Field(
        description="Candidates the engine looked at before filling the list."
    )
    candidates_rejected: int = Field(
        description="Candidates dropped by the evidence filter."
    )
    exhausted: bool = Field(
        description="True when the catalogue held fewer than k valid candidates."
    )


class RecommendationResponse(BaseModel):
    query: QueryMovie
    k: int
    similarity: str
    evidence: EvidenceInfo
    recommendations: list[RecommendationItem]
    skipped: list[SkippedCandidate] = Field(
        default_factory=list,
        description=(
            "Candidates rejected during the scan, with the reason. Surfaced so a "
            "short list is explainable rather than mysterious."
        ),
    )
