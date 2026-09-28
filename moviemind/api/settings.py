"""
Runtime settings for the MovieMind API.

**Environment variables are read; the ``.env`` file is deliberately not.**

The repository's ``.env`` holds the TMDb credential used by
``scripts/fetch_tmdb_snapshot.py``. The API never talks to TMDb — search,
details and recommendations are all served from the local Phase 3 corpus — so
there is no reason for a web process to read a credential file at all. Wiring
``env_file=".env"`` here would make every API process capable of loading a
secret it has no use for, which is exactly the kind of ambient authority that
leaks later. ``MOVIEMIND_*`` variables and real environment variables only.

Every setting has a working default, so ``uvicorn moviemind.api.app:app`` runs
with no configuration at all. There is no database URL, no upstream API base and
no feature flag, because there is nothing behind them to configure.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: Local development origins only. A browser frontend served from Vite or
#: Create React App will be on one of these; nothing else is trusted. This is a
#: default, not a wildcard -- see ``cors_origin_list``.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173,"
    "http://127.0.0.1:5173,"
    "http://localhost:3000,"
    "http://127.0.0.1:3000"
)


class Settings(BaseSettings):
    """API configuration, overridable via ``MOVIEMIND_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="MOVIEMIND_",
        extra="ignore",
        # No env_file. See the module docstring.
    )

    # ---- artifacts ------------------------------------------------------
    #: Phase 3 processed corpus. Read once at startup; never rebuilt, never
    #: written. Building the TF-IDF representation from it happens once, in the
    #: lifespan handler, and costs roughly a second for the 5,000-film snapshot.
    corpus_path: Path = Path("data/processed/movies.jsonl")

    # ---- request limits -------------------------------------------------
    #: Default and ceiling for ``k`` on the recommendations endpoint.
    #:
    #: The ceiling is a real limit, not a formality. The Phase 4 engine ranks the
    #: *entire* candidate set so that its evidence filter can backfill (see
    #: docs/phase-04-recommendation-evaluation.md sec 9), so a request for
    #: k=5000 is answered by scanning 5,000 rows rather than 10. Measured on the
    #: 5,000-film snapshot: 1.0 ms median at k=10, 1.4 ms at k=50, so the scan
    #: itself is nearly free. But response size, the allocation and the client's
    #: rendering work all scale with k, so an unbounded k is still worth refusing.
    default_k: int = Field(default=10, ge=1)
    max_k: int = Field(default=50, ge=1)

    #: Default and ceiling for ``limit`` on the search endpoint.
    default_search_limit: int = Field(default=10, ge=1)
    max_search_limit: int = Field(default=50, ge=1)

    #: Longest accepted search query, in characters. Search is a linear scan
    #: over 5,000 titles, so cost does not grow with query length, but an
    #: unbounded string is still needless work and a needless log line.
    max_query_length: int = Field(default=200, ge=1)

    # ---- CORS -----------------------------------------------------------
    #: Comma-separated. Never a wildcard: a credentialed wildcard is
    #: meaningless and an open one lets any page read a user's results.
    cors_origins: str = DEFAULT_CORS_ORIGINS

    @model_validator(mode="after")
    def _defaults_within_ceilings(self) -> Settings:
        # A default above its own ceiling would make the documented default
        # unusable, which is a configuration error worth failing on at import
        # rather than at the first request.
        if self.default_k > self.max_k:
            raise ValueError(
                f"default_k ({self.default_k}) cannot exceed max_k ({self.max_k})"
            )
        if self.default_search_limit > self.max_search_limit:
            raise ValueError(
                f"default_search_limit ({self.default_search_limit}) cannot exceed "
                f"max_search_limit ({self.max_search_limit})"
            )
        return self

    @property
    def cors_origin_list(self) -> list[str]:
        """Parsed, de-duplicated, order-preserving origin list."""
        seen: dict[str, None] = {}
        for raw in self.cors_origins.split(","):
            origin = raw.strip().rstrip("/")
            if origin:
                seen.setdefault(origin, None)
        return list(seen)

    def describe_limits(self) -> dict[str, int]:
        """The limits a client needs in order to build a valid request."""
        return {
            "default_k": self.default_k,
            "max_k": self.max_k,
            "default_search_limit": self.default_search_limit,
            "max_search_limit": self.max_search_limit,
            "max_query_length": self.max_query_length,
        }
