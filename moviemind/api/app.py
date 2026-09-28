"""
FastAPI application: routing, lifespan, error handling, CORS, logging.

**Thin by construction.** Every route does three things — validate, delegate to
:class:`~moviemind.api.service.CatalogueService`, serialise. There is no
retrieval, scoring, filtering or ranking in this file, and no route touches
:class:`~moviemind.recommend.Recommender` directly. That is the requirement from
the Phase 5 brief expressed as a structural fact rather than a convention: you
cannot accidentally fork the recommendation logic if the route layer has no
access to it.

**Startup loads once.** The corpus is read and vectorised in the lifespan
handler, on a worker thread so the event loop is not blocked, and the resulting
service is shared by every request. Nothing is rebuilt per request, and the
corpus records are held once -- the search structure is an index over them, not
a second copy.
"""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException as StarletteHTTPException

from . import schemas
from .errors import (
    ApiError,
    CatalogueUnavailableError,
    MethodNotAllowedError,
    NotFoundError,
    ValidationError,
    internal_error,
)
from .service import CatalogueLoadError, CatalogueService
from .settings import Settings

logger = logging.getLogger("moviemind.api")

#: URL prefix. Versioned from the first release so a future v2 can coexist.
API_PREFIX = "/api/v1"
API_VERSION = "v1"


def create_app(
    settings: Settings | None = None,
    *,
    service: CatalogueService | None = None,
) -> FastAPI:
    """
    Build the application.

    ``service`` is an injection point for tests: pass a pre-built service and the
    lifespan will not touch the filesystem. Production passes nothing and gets
    the real load-once-from-corpus behaviour.

    **One settings object, not two.** Limit enforcement lives in the service,
    because it is the layer that normalises and range-checks ``k``, ``limit`` and
    the query length; the app's own use of ``settings`` is CORS. If those two
    disagreed, ``/api/v1/meta`` would publish ceilings the routes do not enforce
    -- a silent contract violation. So an injected service supplies the settings
    unless the caller states them explicitly, and a caller that does state them
    must have built the service from the same object.
    """
    settings = settings or (service.settings if service is not None else Settings())

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if service is not None:
            # Injected: the caller already built it (tests, or a future
            # alternative loader). Nothing to do.
            app.state.service = service
            app.state.load_error = None
            logger.info("catalogue injected: %d films", service.recommender.size)
        else:
            # A missing or corrupt corpus is a deployment error. Rather than
            # crash the process, the app comes up *unready*: /ready reports 503
            # with the reason and data routes return 503, while /health stays
            # 200 so a supervisor can still see the process. That keeps the
            # failure diagnosable instead of an opaque boot loop.
            try:
                app.state.service = await run_in_threadpool(CatalogueService.load, settings)
                app.state.load_error = None
            except CatalogueLoadError as exc:
                app.state.service = None
                app.state.load_error = str(exc)
                logger.error("catalogue failed to load: %s", exc)
            except Exception:  # pragma: no cover - defensive
                app.state.service = None
                app.state.load_error = "catalogue failed to load; see server logs"
                logger.exception("unexpected error loading the catalogue")
        try:
            yield
        finally:
            # Release the matrix and corpus on shutdown rather than leaving them
            # to process teardown; makes a reload in a test process clean.
            app.state.service = None
            app.state.load_error = None

    app = FastAPI(
        title="MovieMind API",
        version=API_VERSION,
        summary=(
            "Content-based movie recommendations over a local TMDb snapshot. "
            "No accounts, no database, no outbound network calls."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings

    # ---- CORS -------------------------------------------------------
    # Explicit origin list, never "*". Only GET is exposed, and credentials are
    # not allowed: this API has no cookies or auth, so allowing credentials
    # would widen the blast radius of any future origin mistake for nothing.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=False,
        allow_methods=["GET", "OPTIONS"],
        allow_headers=["Accept", "Content-Type"],
        expose_headers=["X-Request-Duration-Ms"],
        max_age=600,
    )

    # ---- access log -------------------------------------------------
    # Logs the *route template*, not the concrete path, and never the query
    # string, headers or body. A search query is user input, and movie ids are
    # per-request identifiers: an operator debugging a 500 needs the route and
    # the status, not what the user typed. See docs/phase-05-api.md sec 8 for
    # the uvicorn access-log caveat.
    @app.middleware("http")
    async def access_log(request: Request, call_next: Any) -> Any:
        started = time.perf_counter()
        response = await call_next(request)
        elapsed_ms = (time.perf_counter() - started) * 1000
        route = request.scope.get("route")
        template = getattr(route, "path", "unmatched")
        response.headers["X-Request-Duration-Ms"] = f"{elapsed_ms:.1f}"
        logger.info(
            "%s %s -> %d (%.1f ms)",
            request.method,
            template,
            response.status_code,
            elapsed_ms,
        )
        return response

    # ---- error handlers ---------------------------------------------
    def _envelope(error: ApiError) -> JSONResponse:
        return JSONResponse(status_code=error.status_code, content=error.to_payload())

    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return _envelope(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # FastAPI's own 422 body is a list of per-field dicts. It is replaced
        # with the standard envelope so clients have one error shape, and the
        # per-field detail is preserved under `details` because it is genuinely
        # useful for a client fixing a request.
        return _envelope(ValidationError(details={"fields": exc.errors()}))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        # Reached for 404 on an unknown path and 405 on a wrong method, which
        # are routing concerns rather than application ones.
        if exc.status_code == 404:
            wrapped: ApiError = NotFoundError()
        elif exc.status_code == 405:
            wrapped = MethodNotAllowedError()
        else:
            wrapped = ApiError(
                str(exc.detail) if exc.detail else None,
            )
            wrapped.status_code = exc.status_code
            wrapped.code = "http_error"
        return _envelope(wrapped)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # Log the detail server-side; return a fixed body. No traceback, no
        # exception text, no paths reach the client.
        logger.exception("unhandled error: %s", type(exc).__name__)
        return _envelope(internal_error())

    # ---- dependencies ------------------------------------------------
    def get_service(request: Request) -> CatalogueService:
        service = getattr(request.app.state, "service", None)
        if service is None:
            reason = getattr(request.app.state, "load_error", None)
            raise CatalogueUnavailableError(
                details={"reason": reason} if reason else None,
            )
        return service

    ServiceDep = Depends(get_service)

    # ---- routes -------------------------------------------------------
    @app.get(
        f"{API_PREFIX}/health",
        response_model=schemas.HealthResponse,
        tags=["ops"],
        summary="Liveness probe",
    )
    async def health() -> schemas.HealthResponse:
        """Process liveness. Stays 200 even when the catalogue failed to load."""
        return schemas.HealthResponse(api_version=API_VERSION)

    @app.get(
        f"{API_PREFIX}/ready",
        response_model=schemas.ReadyResponse,
        tags=["ops"],
        summary="Readiness probe",
    )
    async def ready(request: Request) -> JSONResponse:
        """
        Readiness. 200 when the index is loaded, 503 when it is not.

        The reason string is the same message that was logged at ERROR, so a
        failed deploy can be diagnosed from the probe alone. It names a path and
        a command, both of which the operator needs; it contains no secrets and
        no traceback.
        """
        service = getattr(request.app.state, "service", None)
        if service is None:
            return JSONResponse(
                status_code=503,
                content=schemas.ReadyResponse(
                    status="not_ready",
                    reason=getattr(request.app.state, "load_error", "catalogue not loaded"),
                ).model_dump(),
            )
        return JSONResponse(
            status_code=200,
            content=schemas.ReadyResponse(
                status="ready",
                catalogue=service.stats(),
            ).model_dump(),
        )

    @app.get(
        f"{API_PREFIX}/meta",
        response_model=schemas.MetaResponse,
        tags=["ops"],
        summary="Locked configuration, limits and TMDb attribution",
    )
    async def meta(service: CatalogueService = ServiceDep) -> schemas.MetaResponse:
        """
        What the API is actually running.

        Publishes the Phase 4 config fingerprint so a client (or a reviewer) can
        confirm the deployed index is the measured one, and carries the TMDb
        attribution notice that the product is required to display.
        """
        return service.meta(API_VERSION)

    # Declared before `/movies/{movie_id}` on purpose: FastAPI matches routes in
    # declaration order, and a literal `search` segment would otherwise be
    # captured by the `{movie_id}` path parameter and fail as a non-integer.
    @app.get(
        f"{API_PREFIX}/movies/search",
        response_model=schemas.SearchResponse,
        tags=["movies"],
        summary="Search films by title",
    )
    async def search_movies(
        q: str = Query(
            ...,
            description=(
                "Case-insensitive substring of a film title. No length bounds are "
                "declared here on purpose: the service owns them, so that empty, "
                "whitespace-only and over-long queries all report their own code "
                "instead of collapsing into one generic validation error."
            ),
        ),
        limit: int | None = Query(
            default=None,
            description="Maximum results. Capped by the server; see /api/v1/meta.",
        ),
        service: CatalogueService = ServiceDep,
    ) -> schemas.SearchResponse:
        """
        Title search.

        An empty or whitespace-only ``q`` is a 400 ``empty_query``, not an empty
        result: it is a malformed request rather than a search that found
        nothing. A search that genuinely matches nothing is a 200 with
        ``results: []`` and ``total_matches: 0``, because "no matches" is a
        successful search, not a failure.
        """
        return service.search(q, limit)

    @app.get(
        f"{API_PREFIX}/movies/{{movie_id}}",
        response_model=schemas.MovieDetail,
        tags=["movies"],
        summary="Film metadata",
    )
    async def movie_details(
        movie_id: int,
        service: CatalogueService = ServiceDep,
    ) -> schemas.MovieDetail:
        """Metadata for one canonical TMDb id. 404 when the id is not indexed."""
        return service.details(movie_id)

    @app.get(
        f"{API_PREFIX}/movies/{{movie_id}}/recommendations",
        response_model=schemas.RecommendationResponse,
        tags=["recommendations"],
        summary="Content-based recommendations for a film",
    )
    async def movie_recommendations(
        movie_id: int,
        k: int | None = Query(
            default=None,
            description="How many results to return. Capped by the server.",
        ),
        service: CatalogueService = ServiceDep,
    ) -> schemas.RecommendationResponse:
        """
        Recommendations for one canonical TMDb id.

        Ordering, query exclusion, evidence filtering and backfill are the
        engine's, unchanged from Phase 4. Returns at most ``k`` results, and
        fewer only when the catalogue held too few candidates at the evidence
        threshold -- which ``evidence.exhausted`` reports explicitly.
        """
        return service.recommend(movie_id, k)

    return app


#: Module-level app for `uvicorn moviemind.api.app:app`.
#: Constructed lazily is not possible here without breaking that target, so this
#: is the real thing; the lifespan handler does the loading, not the import, so
#: importing this module never touches the filesystem.
app = create_app()
