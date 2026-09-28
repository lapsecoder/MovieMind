"""
MovieMind — deterministic content-based feature engineering (Phase 3).

Public surface:

    from moviemind.config import PreprocessConfig, FieldConfig
    from moviemind.pipeline import build_corpus, write_corpus
    from moviemind.representations import REPRESENTATIONS, build_representation

Licence posture carried from Phase 2: TMDb metadata is used for
non-commercial retrieval and similarity only. It must never be used to train a
model, must never be committed to version control, and requires attribution in
any shipped UI.
"""

from __future__ import annotations

from .config import CREDITS_STINGER_KEYWORDS, FieldConfig, PreprocessConfig
from .pipeline import (
    build_corpus,
    build_movie_features,
    extract_cast_tokens,
    extract_director_tokens,
    extract_genre_tokens,
    extract_keyword_tokens,
    extract_overview_tokens,
    iter_raw_records,
    write_corpus,
)
from .representations import (
    REPRESENTATIONS,
    RepresentationResult,
    RepresentationSpec,
    build_representation,
)
from .text import canonical_phrase, normalise_text, phrase_tokens, tokenize

__all__ = [
    "CREDITS_STINGER_KEYWORDS",
    "FieldConfig",
    "PreprocessConfig",
    "REPRESENTATIONS",
    "RepresentationResult",
    "RepresentationSpec",
    "build_corpus",
    "build_movie_features",
    "build_representation",
    "canonical_phrase",
    "extract_cast_tokens",
    "extract_director_tokens",
    "extract_genre_tokens",
    "extract_keyword_tokens",
    "extract_overview_tokens",
    "iter_raw_records",
    "normalise_text",
    "phrase_tokens",
    "tokenize",
    "write_corpus",
]

__version__ = "0.3.0"
