"""
MovieMind API — a thin HTTP surface over the Phase 4 recommendation engine.

**Scope guard.** This package is packaging and nothing else. It contains no
retrieval logic, no similarity computation, no filtering rule and no weight. The
engine in :mod:`moviemind.recommend` remains the single source of truth for what
a recommendation is; routes here translate HTTP into calls on that engine and
back. If a behaviour in this package disagrees with the engine, the engine is
right and this package is a bug.

The one place the API is allowed to be opinionated is the *shape* of a response
and the *validation* of a request. Everything else is delegated.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .settings import Settings

if TYPE_CHECKING:
    from .app import create_app

__all__ = ["Settings", "create_app"]


def __getattr__(name: str) -> Any:
    """Resolve ``create_app`` on first access (PEP 562)."""
    if name == "create_app":
        from .app import create_app

        return create_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
