"""
Representation construction.

Scope limit: this module builds and measures vectors. It never computes
similarity or ranking -- that is Phase 4.
"""

from __future__ import annotations

import dataclasses

import numpy as np
import pytest
import scipy.sparse as sp
from conftest import make_record

from moviemind.config import PreprocessConfig
from moviemind.pipeline import build_movie_features
from moviemind.representations import (
    REPRESENTATIONS,
    RepresentationResult,
    build_representation,
    compare_all,
)


def make_corpus(count: int = 40) -> list[dict]:
    """
    A tiny corpus with controlled vocabulary: a shared block, a
    singleton, and a film with no text at all.
    """
    features = []
    for i in range(count):
        record = make_record(
            movie_id=i,
            title=f"Film {i}",
            overview=(
                "A detective investigates a conspiracy spanning several cities "
                "while an ally struggles with grief and betrayal."
            ),
            keywords=["detective", "conspiracy", f"keywordonly{i}"],
            genres=["Crime", "Drama"],
            cast=[f"Actor{i % 5} Surname{i % 7}", "Shared Star"],
            directors=[f"Director{i % 3} Name"],
        )
        features.append(build_movie_features(record, PreprocessConfig()))
    features.append(build_movie_features(make_record(movie_id=9999, overview=None, keywords=[],
                                                     genres=[], cast=[], directors=[]),
                                         PreprocessConfig()))
    return features


# ---------------------------------------------------------------------------
# shape
# ---------------------------------------------------------------------------
def test_representation_is_a_l2_normalised_csr_matrix():
    result = build_representation(make_corpus(), REPRESENTATIONS[0], PreprocessConfig())
    assert isinstance(result.matrix, sp.csr_matrix)
    assert result.matrix.dtype == np.float32
    norms = np.sqrt(result.matrix.multiply(result.matrix).sum(axis=1)).A.ravel()
    present = norms > 0
    assert np.allclose(norms[present], 1.0, atol=1e-5)


def test_dimensions_and_nonzeros_are_consistent():
    result = build_representation(make_corpus(), REPRESENTATIONS[0], PreprocessConfig())
    assert result.dimensions == result.matrix.shape[1]
    assert result.nonzeros == result.matrix.nnz
    assert result.density_percent == pytest.approx(
        result.matrix.nnz / (result.matrix.shape[0] * result.matrix.shape[1]) * 100
    )


def test_empty_vocabulary_does_not_crash():
    """An all-empty corpus must still yield a usable (0-dimension) result."""
    empty = [build_movie_features(make_record(movie_id=1, overview="x", keywords=[], genres=[],
                                              cast=[], directors=[]), PreprocessConfig())]
    result = build_representation(empty, REPRESENTATIONS[0], PreprocessConfig())
    assert result.dimensions == 0
    assert result.all_zero_documents == 1


# ---------------------------------------------------------------------------
# min_df
# ---------------------------------------------------------------------------
def test_min_df_two_drops_singleton_columns():
    """A keyword present in exactly one film cannot contribute to any
    off-diagonal cosine, so pruning it is lossless."""
    result = build_representation(make_corpus(), REPRESENTATIONS[2], PreprocessConfig())
    assert all(not v.startswith("kw:keywordonly") for v in result.feature_names)


def test_genre_block_is_never_pruned():
    result = build_representation(make_corpus(), REPRESENTATIONS[1], PreprocessConfig())
    assert any(v.startswith("gn:") for v in result.feature_names)


def test_no_unnamespaced_tokens_leak_into_the_vocabulary():
    """Phrase tokens contain no whitespace, so a phrase cannot be shredded
    into a bare, unnamespaced term."""
    for spec in REPRESENTATIONS:
        result = build_representation(make_corpus(), spec, PreprocessConfig())
        for term in result.feature_names:
            assert ":" in term, term
            assert " " not in term, term


def test_science_fiction_survives_as_one_genre_feature():
    corpus = [
        build_movie_features(
            make_record(movie_id=i, genres=["Science Fiction", "TV Movie"]),
            PreprocessConfig(),
        )
        for i in range(5)
    ]
    result = build_representation(corpus, REPRESENTATIONS[1], PreprocessConfig())
    assert "gn:science_fiction" in result.feature_names
    assert "gn:tv_movie" in result.feature_names
    assert "gn:science" not in result.feature_names
    assert "science" not in result.feature_names


# ---------------------------------------------------------------------------
# block modes
# ---------------------------------------------------------------------------
def test_per_block_equal_weights_give_each_field_equal_energy():
    """The known pathology: 19 genre dimensions receive the same L2 energy
    as ~10,000 overview dimensions."""
    config = PreprocessConfig()
    corpus = [
        build_movie_features(
            make_record(
                movie_id=i,
                overview=("A detective investigates a conspiracy spanning several cities "
                          "while an ally struggles with grief and betrayal."),
                genres=["Crime"],
            ),
            config,
        )
        for i in range(30)
    ]
    result = build_representation(corpus, REPRESENTATIONS[1], config)
    share = result.field_contribution_share()
    assert share["overview"] == pytest.approx(share["genres"], abs=1.0)


def test_flat_mode_lets_a_field_follow_its_token_share():
    """
    The contrast with per_block is the whole point: one overview line plus a
    single genre label should leave the genre block as a minor contributor,
    not half the vector.
    """
    config = dataclasses.replace(PreprocessConfig(), block_mode="flat")
    corpus = [
        build_movie_features(
            make_record(
                movie_id=i,
                overview=("A detective investigates a conspiracy spanning several cities "
                          "while an ally struggles with grief and betrayal."),
                genres=["Crime"],
            ),
            config,
        )
        for i in range(30)
    ]
    share = build_representation(
        corpus, REPRESENTATIONS[1], config
    ).field_contribution_share()
    assert share["genres"] < 20.0
    assert share["overview"] > 80.0

    block_share = build_representation(
        corpus, REPRESENTATIONS[1], PreprocessConfig()
    ).field_contribution_share()
    assert share["genres"] < block_share["genres"] / 2


def test_flat_mode_uses_the_strictest_min_df():
    """Genres are configured min_df=1, but in flat mode a single min_df serves
    every field, so taking the minimum would admit singletons corpus-wide."""
    config = dataclasses.replace(PreprocessConfig(), block_mode="flat")
    corpus = make_corpus()
    # Pre-condition: the corpus really does contain singleton keyword phrases.
    singletons = [
        t for item in corpus for t in item["tokens"]["keywords"] if t.startswith("kw:keywordonly")
    ]
    assert len(singletons) == len(set(singletons)) > 1
    result = build_representation(corpus, REPRESENTATIONS[3], config)
    assert all(not v.startswith("kw:keywordonly") for v in result.feature_names)
    # The decisive check: total dimensions match per_block, i.e. the looser
    # per-field floor for genres did not re-admit singletons anywhere.
    per_block = build_representation(corpus, REPRESENTATIONS[3], PreprocessConfig())
    assert result.dimensions == per_block.dimensions
    assert sum(b.dimensions for b in result.blocks) == per_block.dimensions


def test_block_dimensions_sum_to_total_in_flat_mode():
    config = dataclasses.replace(PreprocessConfig(), block_mode="flat")
    result = build_representation(make_corpus(), REPRESENTATIONS[3], config)
    assert sum(b.dimensions for b in result.blocks) == result.dimensions


def test_every_block_matrix_has_one_row_per_document():
    config = dataclasses.replace(PreprocessConfig(), block_mode="flat")
    corpus = make_corpus()
    result = build_representation(corpus, REPRESENTATIONS[3], config)
    for block in result.blocks:
        assert block.matrix.shape[0] == len(corpus)


def test_unknown_block_mode_is_rejected():
    config = dataclasses.replace(PreprocessConfig(), block_mode="sideways")
    with pytest.raises(ValueError):
        build_representation(make_corpus(), REPRESENTATIONS[0], config)


# ---------------------------------------------------------------------------
# coverage reporting
# ---------------------------------------------------------------------------
def test_documents_with_tokens_never_exceeds_the_corpus():
    result = build_representation(make_corpus(), REPRESENTATIONS[3], PreprocessConfig())
    for block in result.blocks:
        assert 0 <= block.documents_with_tokens <= len(make_corpus())


def test_coverage_is_monotonic_across_candidates():
    """
    Adding a field can only add documents, so the all-zero count must be
    non-increasing from A to D. This is a property of the field set, not of any
    tuning, so it must hold in both modes.

    The synthetic corpus deliberately contains one film with no text at all, so
    the count does not reach zero even for D; in the real snapshot D covers all
    5,000 films.
    """
    for mode in ("per_block", "flat"):
        config = dataclasses.replace(PreprocessConfig(), block_mode=mode)
        corpus = make_corpus()
        zeros = [
            build_representation(corpus, spec, config).all_zero_documents
            for spec in REPRESENTATIONS
        ]
        assert zeros == sorted(zeros, reverse=True), mode
        assert zeros[0] >= zeros[-1], mode


# ---------------------------------------------------------------------------
# reporting
# ---------------------------------------------------------------------------
def test_compare_all_covers_every_candidate_in_both_modes():
    corpus = make_corpus()
    fields = {"overview", "keywords", "genres", "cast", "director"}
    for mode in ("per_block", "flat"):
        config = dataclasses.replace(PreprocessConfig(), block_mode=mode)
        results = compare_all(corpus, config)
        assert len(results) == len(REPRESENTATIONS), mode
        assert [r.spec.key for r in results] == [s.key for s in REPRESENTATIONS]
        for entry in results:
            assert entry.dimensions >= 0
            assert set(entry.field_contribution_share()) <= fields
            assert 0.0 <= entry.density_percent <= 100.0


def test_result_serialises_to_plain_json_types():
    import json

    result = build_representation(make_corpus(), REPRESENTATIONS[3], PreprocessConfig())
    json.dumps(result.as_dict())
