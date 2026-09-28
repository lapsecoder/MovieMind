"""
API error types and the single error envelope every failure travels in.

**Why one envelope.** A client that has to special-case FastAPI's default 422
shape *and* an ``HTTPException`` shape *and* an unhandled-exception 500 is a
client with three parsers. Every error leaving this API has the same body::

    {"error": {"code": "...", "message": "...", "status": 404, "details": {...}}}

``code`` is the stable, machine-readable part; clients branch on it. ``message``
is for humans and may be reworded. ``details`` is optional structured context.

**What never leaves.** No stack traces, no filesystem paths beyond the ones a
user must act on, no environment values, no credentials. The unhandled-exception
handler logs the traceback server-side and returns a fixed message with a
correlation-free generic body; see :func:`internal_error`.
"""

from __future__ import annotations

from typing import Any

#: Stable machine-readable codes. Documented in docs/phase-05-api.md; clients
#: are expected to branch on these strings, so they are treated as API surface.
CODE_EMPTY_QUERY = "empty_query"
CODE_QUERY_TOO_LONG = "query_too_long"
CODE_INVALID_K = "invalid_k"
CODE_INVALID_LIMIT = "invalid_limit"
CODE_UNKNOWN_MOVIE = "unknown_movie"
CODE_INSUFFICIENT_FEATURES = "insufficient_features"
CODE_VALIDATION_ERROR = "validation_error"
CODE_CATALOGUE_UNAVAILABLE = "catalogue_unavailable"
CODE_INTERNAL_ERROR = "internal_error"
CODE_NOT_FOUND = "not_found"
CODE_METHOD_NOT_ALLOWED = "method_not_allowed"


class ApiError(Exception):
    """
    Base class for every error this API reports deliberately.

    Subclasses fix ``code`` and ``status_code`` so that a route can raise the
    right thing by construction rather than by remembering the mapping.
    """

    code: str = CODE_INTERNAL_ERROR
    status_code: int = 500
    default_message: str = "Unexpected error."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.default_message
        self.details = details
        super().__init__(self.message)

    def to_payload(self) -> dict[str, Any]:
        body: dict[str, Any] = {
            "code": self.code,
            "message": self.message,
            "status": self.status_code,
        }
        if self.details:
            body["details"] = self.details
        return {"error": body}


class EmptyQueryError(ApiError):
    code = CODE_EMPTY_QUERY
    status_code = 400
    default_message = "Query parameter 'q' must contain at least one non-whitespace character."


class QueryTooLongError(ApiError):
    code = CODE_QUERY_TOO_LONG
    status_code = 400
    default_message = "Query parameter 'q' is too long."


class InvalidKError(ApiError):
    code = CODE_INVALID_K
    status_code = 422
    default_message = "k must be an integer within the allowed range."


class InvalidLimitError(ApiError):
    code = CODE_INVALID_LIMIT
    status_code = 422
    default_message = "limit must be an integer within the allowed range."


class UnknownMovieError(ApiError):
    code = CODE_UNKNOWN_MOVIE
    status_code = 404
    default_message = "No film with that TMDb id is in the catalogue."


class InsufficientFeaturesError(ApiError):
    """
    The film exists but has an all-zero feature vector.

    This is a 422 rather than a 404 on purpose: the id resolved, the catalogue
    is fine, and the request cannot be served. It is also a *data* condition
    rather than a client mistake, which the message says explicitly so a UI can
    explain it instead of showing an error.
    """

    code = CODE_INSUFFICIENT_FEATURES
    status_code = 422
    default_message = (
        "This film has no overview, keywords, genres, cast or director tokens, "
        "so nothing can be matched to it."
    )


class CatalogueUnavailableError(ApiError):
    """The recommendation index failed to load, so no data route can serve."""

    code = CODE_CATALOGUE_UNAVAILABLE
    status_code = 503
    default_message = (
        "The recommendation index is not available. The service started but "
        "could not load its catalogue."
    )


class ValidationError(ApiError):
    code = CODE_VALIDATION_ERROR
    status_code = 422
    default_message = "Request parameters failed validation."


class NotFoundError(ApiError):
    code = CODE_NOT_FOUND
    status_code = 404
    default_message = "No such endpoint."


class MethodNotAllowedError(ApiError):
    code = CODE_METHOD_NOT_ALLOWED
    status_code = 405
    default_message = "Method not allowed for this endpoint."


def internal_error() -> ApiError:
    """
    The response for an exception nobody anticipated.

    Carries no detail whatsoever, deliberately: the specifics go to the server
    log where an operator can correlate them with the request, and the client
    gets a fixed body it cannot use to fingerprint the deployment.
    """
    return ApiError(
        "The server encountered an unexpected error while handling this request.",
        details=None,
    )
