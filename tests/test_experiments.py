"""
Tests for the experiment matrix and its manifest.

The matrix is the reproducibility contract for Phase 4, so the tests check that
every experiment is fully described, that known-degenerate configurations are
excluded, and that the two silent-failure modes found during the real run cannot
come back.
"""

from __future__ import annotations

import json

import pytest

from moviemind.config import PreprocessConfig
from moviemind.experiments import (
    ALL_FIELDS,
    CAST_TOP_K_SWEEP,
    GENRE_WEIGHT_SWEEP,
    MIN_SHARED_TERMS_SWEEP,
    SELECTED_GENRE_WEIGHT,
    SELECTED_MIN_SHARED_TERMS,
    build_experiment_matrix,
    experiment_manifest,
    popularity_percentiles,
    weight_for_target_share,
)


@pytest.fixture
def specs():
    return build_experiment_matrix(PreprocessConfig())


# -- the sweep constants themselves are the brief's requirements ------------
def test_genre_sweep_covers_the_mandated_range():
    """The brief mandates this exact set of weights, and 0.44 is the value Phase
    3 derived rather than one chosen after seeing these results."""
    assert GENRE_WEIGHT_SWEEP == (0.0, 0.1, 0.2, 0.3, 0.44, 0.6, 0.8, 1.0)
    assert 0.0 in GENRE_WEIGHT_SWEEP and 1.0 in GENRE_WEIGHT_SWEEP


def test_sweeps_bracket_their_operating_point():
    assert min(CAST_TOP_K_SWEEP) < PreprocessConfig().cast.top_k < max(CAST_TOP_K_SWEEP)
    assert 1 in MIN_SHARED_TERMS_SWEEP
    assert max(MIN_SHARED_TERMS_SWEEP) > 1


def test_weight_for_target_share_inverts_the_energy_relation():
    """A block's influence scales with weight**2 over a share budget, so the
    inverse is the square-root odds ratio. Verified against Phase 3's measured
    21.11% genre share at weight 1.0."""
    assert weight_for_target_share(0.0, 0.2111) == 0.0
    assert weight_for_target_share(0.2111, 0.2111) == pytest.approx(1.0)
    # Inverting 21.11% down to a 5% genre budget yields 0.4435, which is why
    # the mandated sweep contains 0.44 and not a rounder-looking number. The
    # Phase 4 run then measured 5.07% actual genre energy at weight 0.44, so
    # the prediction held.
    assert weight_for_target_share(0.05, 0.2111) == pytest.approx(0.4435, abs=1e-3)
    assert min(GENRE_WEIGHT_SWEEP, key=lambda w: abs(w - 0.4435)) == 0.44
    with pytest.raises(ValueError):
        weight_for_target_share(1.0, 0.2111)
    with pytest.raises(ValueError):
        weight_for_target_share(0.05, 0.0)


# -- matrix shape ----------------------------------------------------------
def test_matrix_covers_every_required_family(specs):
    families = {s.family for s in specs}
    assert families == {
        "representation",
        "genre_sweep",
        "cast_top_k",
        "ablation",
        "single_field",
        "min_shared_terms",
        "selected",
    }


def test_matrix_includes_the_overview_only_baseline(specs):
    """The brief requires a baseline that uses overview text alone."""
    assert any(s.representation_key == "A" for s in specs)
    assert any(s.name == "rep_A" for s in specs)


def test_every_field_is_ablated_and_also_used_alone(specs):
    for name in ALL_FIELDS:
        assert any(s.name == f"drop_{name}" for s in specs)
        assert any(s.name == f"only_{name}" for s in specs)


def test_experiment_names_are_unique(specs):
    names = [s.name for s in specs]
    assert len(names) == len(set(names))


def test_family_filters_narrow_the_matrix(specs):
    genre = [s for s in specs if s.family == "genre_sweep"]
    assert len(genre) == len(GENRE_WEIGHT_SWEEP)
    assert all(s.family == "genre_sweep" for s in genre)


# -- every experiment must be fully specified ------------------------------
def test_every_spec_declares_a_question_and_hypothesis(specs):
    for spec in specs:
        assert spec.question.strip()
        assert spec.hypothesis.strip()
        assert spec.config.fingerprint()


def test_genre_sweep_varies_only_the_genre_weight(specs):
    """A sweep that silently changed something else would not be a sweep."""
    genre = [s for s in specs if s.family == "genre_sweep"]
    reference = genre[0].config
    for spec in genre:
        for name in ALL_FIELDS:
            if name == "genres":
                continue
            assert spec.config.fields()[name] == reference.fields()[name]
        assert spec.config.block_mode == reference.block_mode
        assert spec.config.cast.top_k == reference.cast.top_k
        assert spec.representation_key == genre[0].representation_key


def test_cast_sweep_varies_only_the_cast_bound(specs):
    cast = [s for s in specs if s.family == "cast_top_k"]
    assert [s.config.cast.top_k for s in cast] == list(CAST_TOP_K_SWEEP)
    reference = cast[0].config
    for spec in cast:
        for name in ALL_FIELDS:
            if name == "cast":
                continue
            assert spec.config.fields()[name] == reference.fields()[name]


# -- regression: the two silent-failure modes from the real run ------------
def test_cast_top_k_experiments_rebuild_features(specs):
    """`cast.top_k` is applied during tokenisation, not vectorisation. Running
    the sweep against the already-built corpus produced four byte-identical
    experiments in the first Phase 4 run; these specs must rebuild."""
    cast = [s for s in specs if s.family == "cast_top_k"]
    assert cast
    assert all(s.rebuild_features for s in cast)
    # Sanity: they really would be identical without the rebuild flag.
    assert len({s.config.fingerprint() for s in cast}) == len(CAST_TOP_K_SWEEP)


def test_only_the_cast_family_needs_a_rebuild(specs):
    for spec in specs:
        if spec.family == "cast_top_k":
            assert spec.rebuild_features
        else:
            assert not spec.rebuild_features, spec.name


def test_evidence_sweep_varies_the_filter_not_the_features(specs):
    evidence = [s for s in specs if s.family == "min_shared_terms"]
    assert [s.min_shared_terms for s in evidence] == list(MIN_SHARED_TERMS_SWEEP)
    # Feature space held constant: identical fingerprints throughout.
    assert len({s.config.fingerprint() for s in evidence}) == 1
    assert not any(s.rebuild_features for s in evidence)


def test_only_the_evidence_family_varies_the_filter(specs):
    for spec in specs:
        if spec.family in ("min_shared_terms", "selected"):
            continue
        assert spec.min_shared_terms == 1, spec.name


def test_selected_reproduces_the_two_sweep_decisions(specs):
    """The shipped configuration must be the exact combination chosen from the
    two sweeps, not a separately invented third value."""
    selected = [s for s in specs if s.family == "selected"]
    assert len(selected) == 1
    spec = selected[0]
    assert spec.config.genres.weight == SELECTED_GENRE_WEIGHT
    assert SELECTED_GENRE_WEIGHT in GENRE_WEIGHT_SWEEP
    assert spec.min_shared_terms == SELECTED_MIN_SHARED_TERMS
    assert SELECTED_MIN_SHARED_TERMS in MIN_SHARED_TERMS_SWEEP
    assert spec.representation_key == "custom:" + "+".join(ALL_FIELDS)
    assert not spec.rebuild_features


# -- popularity percentiles -------------------------------------------------
def test_popularity_percentiles_span_zero_to_hundred():
    corpus = [{"id": i, "popularity": float(i)} for i in range(1, 101)]
    percentiles = popularity_percentiles(corpus)
    assert percentiles[1] == 0.0
    assert percentiles[100] == 100.0
    assert percentiles[50] == pytest.approx(49.497, abs=0.01)


def test_popularity_percentiles_average_ties():
    """Two films with identical popularity must not be treated as different, or
    a tie becomes a spurious rank signal."""
    corpus = [
        {"id": 1, "popularity": 5.0},
        {"id": 2, "popularity": 5.0},
        {"id": 3, "popularity": 1.0},
    ]
    percentiles = popularity_percentiles(corpus)
    assert percentiles[1] == percentiles[2]
    assert percentiles[3] == 0.0


def test_popularity_percentiles_tolerate_missing_values():
    corpus = [{"id": 1, "popularity": None}, {"id": 2}, {"id": 3, "popularity": 9.0}]
    percentiles = popularity_percentiles(corpus)
    assert set(percentiles) == {1, 2, 3}
    assert percentiles[3] == 100.0


def test_popularity_percentiles_of_empty_corpus_is_empty():
    assert popularity_percentiles([]) == {}


# -- manifest --------------------------------------------------------------
def test_manifest_records_that_no_seed_is_needed(specs):
    """The protocol is fully deterministic, so there is nothing for a seed to
    control. It is recorded as null rather than fabricated."""
    payload = experiment_manifest(
        [], corpus_path="x", corpus_sha256="y", labels_path="z", k=10,
        protocol="p", seed=None,
    )
    assert payload["seed"] is None
    assert "deterministic" in payload["determinism"].lower()
    assert payload["top_k"] == 10
    assert payload["similarity"].startswith("cosine")


def test_manifest_is_json_serialisable(tmp_path, specs):
    payload = experiment_manifest(
        [], corpus_path="x", corpus_sha256="y", labels_path="z", k=10,
        protocol="p", seed=None, notes="n",
    )
    path = tmp_path / "m.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    assert json.loads(path.read_text(encoding="utf-8"))["experiments"] == []
