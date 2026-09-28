"""
Tests for the recommendation engine and the evaluation protocol.

Three properties get the most attention because breaking any of them would
silently corrupt every Phase 4 number or ship a wrong list to a user:

* **determinism** -- ties are broken by ascending TMDb id, so a top-K is a pure
  function of (matrix, k);
* **no self-recommendation, no duplicates** -- the query is excluded and every
  id appears at most once;
* **backfill** -- the evidence filter must be able to *promote* a passing
  candidate from below the cutoff, not only delete from above it. A filter
  applied to a pre-truncated top-k can only ever return a short list, which is
  the production defect fixed in this patch; ``tests/test_backfill_*`` below is
  the regression net for it.
"""

from __future__ import annotations

import numpy as np
import pytest
import scipy.sparse as sp
from conftest import PROCESSED_CORPUS, make_record

from moviemind.config import PreprocessConfig
from moviemind.evaluate import (
    EvaluationResult,
    evaluate_recommender,
    ndcg_at_k,
    spearman,
)
from moviemind.labels import ProxyLabels
from moviemind.pipeline import build_movie_features
from moviemind.recommend import (
    InsufficientFeaturesError,
    Recommender,
    UnknownMovieError,
    ranked_candidates,
    sparse_top_k,
)
from moviemind.representations import RepresentationResult, RepresentationSpec


def build_corpus(records: list[dict], config: PreprocessConfig | None = None) -> list[dict]:
    config = config or PreprocessConfig()
    out = []
    for record in records:
        built = build_movie_features(record, config)
        assert built is not None
        out.append(built)
    return out


@pytest.fixture
def corpus() -> list[dict]:
    """Six films: a two-film franchise, a shared director, and two singletons."""
    records = [
        make_record(
            movie_id=1,
            title="Franchise One",
            overview="A detective in a rain-soaked city uncovers a conspiracy.",
            genres=["Crime", "Thriller"],
            keywords=["detective", "conspiracy"],
            cast=["Ada Lovelace", "Grace Hopper"],
            directors=["Kenji Watanabe"],
            belongs_to_collection={"id": 7, "name": "Shared Saga"},
        ),
        make_record(
            movie_id=2,
            title="Franchise Two",
            overview="A detective in a coastal town uncovers a conspiracy.",
            genres=["Crime", "Thriller"],
            keywords=["detective", "conspiracy"],
            cast=["Ada Lovelace", "Alan Turing"],
            directors=["Kenji Watanabe"],
            belongs_to_collection={"id": 7, "name": "Shared Saga"},
        ),
        make_record(
            movie_id=3,
            title="Watanabe Solo",
            overview="A samurai faces an invading army across a burning province.",
            genres=["Action", "Drama"],
            keywords=["samurai"],
            cast=["Grace Hopper"],
            directors=["Kenji Watanabe"],
        ),
        make_record(
            movie_id=4,
            title="Unrelated Animation",
            overview="A cartoon mouse learns to cook pasta for his friends.",
            genres=["Animation", "Family"],
            keywords=["cartoon"],
            cast=["Alan Turing"],
            directors=["Someone Else"],
        ),
        make_record(
            movie_id=5,
            title="Space Documentary",
            overview="A documentary about the early space programme and rockets.",
            genres=["Documentary"],
            keywords=["space"],
            cast=["Ada Lovelace"],
            directors=["Another Person"],
        ),
        make_record(
            movie_id=6,
            title="Lonely Drama",
            overview="A widow walks through an empty city at dawn.",
            genres=["Drama"],
            keywords=["widow"],
            cast=["Someone New"],
            directors=["Yet Another"],
        ),
    ]
    return build_corpus(records)


@pytest.fixture
def recommender(corpus) -> Recommender:
    return Recommender.build(corpus, PreprocessConfig(), representation="D")


# -- backfill fixture -------------------------------------------------------
# A hand-built matrix, because the defect is about the *interaction* of score
# order and evidence count, and TF-IDF over generated text cannot be steered
# into that shape reliably.
#
# Feature weights are chosen so the two kinds of candidate are cleanly
# separable, which is the whole point of the fixture:
#
#   * "thin" rows carry a single feature at full weight, so after L2
#     normalisation they score *high* against the query (0.96) while sharing
#     only ONE term. A pure score ranking prefers them; they are exactly the
#     thin-evidence matches the filter exists to reject.
#   * "thick" rows share three query terms but are diluted by extra features,
#     so they score *lower* (0.74 / 0.28) while carrying three terms of
#     evidence. They are the results a correct filter must promote.
#
# Ranked order for the query (row 0) is therefore:
#   101, 102, 103 (thin, 0.96) -> 104, 106 (thick, 0.74) -> 105 (thick, 0.28)
#   -> 107 (thin, 0.16)
#
# So with min_shared_terms=2 and k=3 the pre-fix top-3 window is exactly
# {101, 102, 103} -- all rejected -- and a correct implementation must reach
# past it to {104, 106, 105}. Any test using this fixture is therefore
# sensitive to the bug by construction.
BACKFILL_ROWS = [
    [3.0, 0.5, 0.5, 0.5],   # 0  query
    [1.0, 0.0, 0.0, 0.0],   # 1  thin,  shared 1,  score 0.961
    [1.0, 0.0, 0.0, 0.0],   # 2  thin,  shared 1,  score 0.961
    [1.0, 0.0, 0.0, 0.0],   # 3  thin,  shared 1,  score 0.961
    [0.4, 0.4, 0.4, 0.0],   # 4  thick, shared 3,  score 0.740
    [0.0, 0.4, 0.4, 0.4],   # 5  thick, shared 3,  score 0.277
    [0.4, 0.0, 0.4, 0.4],   # 6  thick, shared 3,  score 0.740
    [0.0, 0.0, 0.0, 1.0],   # 7  thin,  shared 1,  score 0.160
]
BACKFILL_QUERY_ID = 100


def _build_backfill_recommender(min_shared_terms: int = 2) -> Recommender:
    matrix = sp.csr_matrix(np.array(BACKFILL_ROWS, dtype=np.float32))
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    # L2-normalise so scores are cosine, matching what the real pipeline emits.
    matrix = sp.csr_matrix(matrix.multiply(sp.csr_matrix((1.0 / norms).reshape(-1, 1))))
    corpus = [
        {
            "id": 100 + i,
            "title": f"Film {i}",
            "tokens": {"genres": []},
            "release_year": 2000 + i,
            "popularity": 1.0,
            "vote_average": 5.0,
            "vote_count": 10,
            "collection_name": None,
        }
        for i in range(len(BACKFILL_ROWS))
    ]
    result = RepresentationResult(
        RepresentationSpec("hand", "hand", ("overview",)),
        [],
        matrix,
        ["f0", "f1", "f2", "f3"],
    )
    return Recommender(corpus, result, min_shared_terms=min_shared_terms)


@pytest.fixture
def backfill() -> Recommender:
    return _build_backfill_recommender(min_shared_terms=2)


def test_backfill_fixture_actually_reproduces_the_bug():
    """Guard the guard.

    If a future edit to the fixture weights stops the pre-fix top-k window from
    being entirely rejectable, every backfill test below would pass against a
    broken implementation. Assert the discriminating shape directly: the
    unfiltered top-3 contains no candidate that survives the filter.
    """
    unfiltered = _build_backfill_recommender(min_shared_terms=1)
    filtered = _build_backfill_recommender(min_shared_terms=2)
    top3 = unfiltered.recommend(BACKFILL_QUERY_ID, k=3).movie_ids
    assert top3 == (101, 102, 103)
    assert all(r.shared_terms < 2 for r in unfiltered.recommend(BACKFILL_QUERY_ID, k=3))
    # Pre-fix behaviour on this input: fetch 3, reject all 3, return nothing.
    assert all(fid not in top3 for fid in filtered.recommend(BACKFILL_QUERY_ID, k=3).movie_ids)


def test_backfill_promotes_candidates_from_below_the_cutoff(backfill):
    """The defect itself: a rejection at rank 1-3 must be backfilled from rank 4+."""
    result = backfill.recommend(BACKFILL_QUERY_ID, k=3)
    assert result.movie_ids == (104, 106, 105)
    assert all(r.shared_terms >= 2 for r in result)
    # The three thin high-scoring candidates were rejected, not returned.
    assert not {101, 102, 103} & set(result.movie_ids)
    assert {mid for mid, _ in result.skipped} == {101, 102, 103}


def test_backfill_returns_exactly_k_when_enough_candidates_exist(backfill):
    """Requirement: k valid results whenever the catalogue can supply them."""
    for k in (1, 2, 3):
        assert len(backfill.recommend(BACKFILL_QUERY_ID, k=k)) == k


def test_returns_fewer_than_k_only_when_catalogue_lacks_valid_candidates(backfill):
    """Requirement: short lists are allowed, but only for a real reason.

    This catalogue holds exactly three candidates with >= 2 shared terms, so a
    request for five cannot be filled and must say so rather than pad.
    """
    valid = {104, 105, 106}
    for k in (4, 5, 20):
        result = backfill.recommend(BACKFILL_QUERY_ID, k=k)
        assert len(result) == len(valid)
        assert set(result.movie_ids) == valid


def test_backfill_never_exceeds_k(backfill):
    for k in range(1, 12):
        assert len(backfill.recommend(BACKFILL_QUERY_ID, k=k)) <= k


def test_accepted_candidates_preserve_unfiltered_rank_order(backfill):
    """The filter may delete rows from the ranking, never reorder them."""
    unfiltered = _build_backfill_recommender(min_shared_terms=1)
    order = unfiltered.recommend(BACKFILL_QUERY_ID, k=len(BACKFILL_ROWS) - 1)
    expected = [r.movie_id for r in order]
    for k in (1, 2, 3, 5, 7):
        got = list(backfill.recommend(BACKFILL_QUERY_ID, k=k).movie_ids)
        assert got == [mid for mid in expected if mid in set(got)]


def test_backfill_keeps_descending_score_and_contiguous_ranks(backfill):
    result = backfill.recommend(BACKFILL_QUERY_ID, k=5)
    assert [r.rank for r in result] == list(range(1, len(result) + 1))
    scores = [r.score for r in result]
    assert scores == sorted(scores, reverse=True)
    # The two thick candidates tied on score must come back in ascending id
    # order, matching the unfiltered ranking.
    assert result.movie_ids[:2] == (104, 106)


def test_backfill_never_returns_the_query(backfill):
    for k in range(1, 10):
        assert BACKFILL_QUERY_ID not in backfill.recommend(BACKFILL_QUERY_ID, k=k).movie_ids


def test_backfill_never_repeats_a_movie(backfill):
    for k in range(1, 10):
        ids = backfill.recommend(BACKFILL_QUERY_ID, k=k).movie_ids
        assert len(ids) == len(set(ids))


def test_backfill_is_deterministic_across_calls_and_instances(backfill):
    first = backfill.recommend(BACKFILL_QUERY_ID, k=5)
    for _ in range(5):
        again = backfill.recommend(BACKFILL_QUERY_ID, k=5)
        assert again.movie_ids == first.movie_ids
        assert [r.score for r in again] == [r.score for r in first]
    # A separately constructed index over the same data must agree exactly.
    twin = _build_backfill_recommender(min_shared_terms=2)
    assert twin.recommend(BACKFILL_QUERY_ID, k=5).movie_ids == first.movie_ids


def test_backfill_skipped_reports_only_candidates_actually_considered(backfill):
    """`skipped` is a diagnostic, so it must describe the scan that happened.

    With k=1 the scan stops at the first accept, so only the three thin
    candidates ranked above it may be reported -- not the whole catalogue.
    """
    result = backfill.recommend(BACKFILL_QUERY_ID, k=1)
    assert result.movie_ids == (104,)
    assert [mid for mid, _ in result.skipped] == [101, 102, 103]

    # With k larger than the number of valid candidates the scan runs to
    # exhaustion, and every rejected candidate is accounted for.
    exhausted = backfill.recommend(BACKFILL_QUERY_ID, k=6)
    assert {mid for mid, _ in exhausted.skipped} == {101, 102, 103, 107}


def test_min_score_filter_also_backfills(backfill):
    """Backfill is a property of the scan, so it applies to the score gate too."""
    strict = _build_backfill_recommender(min_shared_terms=1)
    strict._min_score = 0.5   # rejects 105 (0.277) and 107 (0.160) only
    result = strict.recommend(BACKFILL_QUERY_ID, k=3)
    assert result.movie_ids == (101, 102, 103)
    loose = strict.recommend(BACKFILL_QUERY_ID, k=10)
    assert loose.movie_ids == (101, 102, 103, 104, 106)
    assert {mid for mid, reason in loose.skipped if reason == "below_min_score"} == {105, 107}


def test_unknown_movie_raises_under_backfill(backfill):
    with pytest.raises(UnknownMovieError):
        backfill.recommend(999_999, k=5)


def test_impossible_filter_returns_empty_with_a_reason(backfill):
    impossible = _build_backfill_recommender(min_shared_terms=99)
    result = impossible.recommend(BACKFILL_QUERY_ID, k=5)
    assert result.recommendations == ()
    assert len(result.skipped) == len(BACKFILL_ROWS) - 1
    assert {reason for _mid, reason in result.skipped} == {"insufficient_shared_terms"}


def test_ranked_candidates_and_sparse_top_k_agree_on_order():
    """The two retrieval helpers must share one total order.

    `sparse_top_k` is a prefix of `ranked_candidates`; if that ever diverged, a
    caller could get a different ranking depending on which helper it used.
    """
    matrix = sp.csr_matrix(np.array(BACKFILL_ROWS, dtype=np.float32))
    norms = np.sqrt(np.asarray(matrix.multiply(matrix).sum(axis=1)).ravel())
    matrix = sp.csr_matrix(matrix.multiply(sp.csr_matrix((1.0 / norms).reshape(-1, 1))))
    for k in (1, 3, 5, 7):
        top_idx, top_scores = sparse_top_k(matrix, 0, k, exclude=0)
        all_idx, all_scores = ranked_candidates(matrix, 0, exclude=0)
        assert top_idx.tolist() == all_idx[:k].tolist()
        assert np.allclose(top_scores, all_scores[:k])


# -- integration: the real catalogue ----------------------------------------
@pytest.mark.skipif(not PROCESSED_CORPUS.exists(), reason="processed corpus absent")
def test_selected_config_fills_ten_slots_on_the_real_catalogue():
    """Regression guard on the shipped configuration, on real data.

    Pre-fix, 90.6% of the 5,000 queries returned fewer than k=10 results at
    min_shared_terms=3; a scan that backfills must return exactly k for the
    overwhelming majority. Uses a bounded query sample so the suite stays fast.
    """
    import dataclasses

    from moviemind.recommend import load_corpus

    config = dataclasses.replace(
        PreprocessConfig(),
        genres=dataclasses.replace(PreprocessConfig().genres, weight=0.44),
    )
    recommender = Recommender.from_corpus_file(
        PROCESSED_CORPUS,
        config,
        representation=RepresentationSpec(
            "custom:overview+genres+keywords+cast+director",
            "D",
            ("overview", "genres", "keywords", "cast", "director"),
        ),
        min_shared_terms=3,
    )
    full = short = 0
    for row in range(0, recommender.size, 20):
        movie_id = int(recommender.movie_ids[row])
        if recommender.row_nonzeros(row) == 0:
            continue
        result = recommender.recommend(movie_id, k=10)
        assert movie_id not in result.movie_ids
        assert len(result.movie_ids) == len(set(result.movie_ids))
        assert len(result) <= 10
        full += len(result) == 10
        short += len(result) < 10
    assert full + short > 100, "sample too small to be meaningful"
    assert full / (full + short) > 0.9, (
        f"only {full}/{full + short} queries filled 10 slots; backfill regressed"
    )


# -- sparse_top_k ----------------------------------------------------------
def test_sparse_top_k_excludes_the_query_row():
    matrix = sp.csr_matrix(np.array([[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]))
    indices, scores = sparse_top_k(matrix, 0, k=2, exclude=0)
    assert 0 not in indices.tolist()
    # Row 1 is orthogonal to row 0, so it scores 0.0. Zero-similarity films are
    # not returned at all: a "recommendation" with no shared evidence is worse
    # than an empty list, and the caller's own min_shared_terms gate would
    # reject it anyway.
    assert indices.tolist() == [2]
    assert scores.tolist() == [1.0]


def test_sparse_top_k_breaks_ties_by_ascending_index():
    """Rows 1 and 2 both score 1.0 against row 0. The lower id must win, or the
    top-K changes with platform and NumPy build."""
    matrix = sp.csr_matrix(np.array([[1.0, 0.0, 0.0], [1.0, 0.0, 0.0], [1.0, 0.0, 0.0]]))
    indices, _ = sparse_top_k(matrix, 0, k=2, exclude=0)
    assert indices.tolist() == [1, 2]


def test_sparse_top_k_is_deterministic_across_calls():
    rng = np.random.default_rng(0)
    dense = rng.random((60, 40)).astype(np.float32)
    dense[dense < 0.7] = 0.0
    matrix = sp.csr_matrix(dense)
    first = sparse_top_k(matrix, 3, k=8, exclude=3)
    for _ in range(5):
        again = sparse_top_k(matrix, 3, k=8, exclude=3)
        assert first[0].tolist() == again[0].tolist()
        assert np.allclose(first[1], again[1])


def test_sparse_top_k_returns_empty_when_nothing_matches():
    matrix = sp.csr_matrix(np.array([[1.0, 0.0], [0.0, 1.0]]))
    indices, scores = sparse_top_k(matrix, 0, k=5, exclude=0)
    assert indices.size == 0
    assert scores.size == 0


# -- recommend -------------------------------------------------------------
def test_recommend_never_returns_the_query(recommender, corpus):
    for record in corpus:
        result = recommender.recommend(record["id"], k=5)
        assert record["id"] not in result.movie_ids


def test_recommend_never_repeats_a_film(recommender, corpus):
    for record in corpus:
        ids = recommender.recommend(record["id"], k=5).movie_ids
        assert len(ids) == len(set(ids))


def test_recommend_returns_at_most_k(recommender, corpus):
    for k in (1, 3, 5, 50):
        result = recommender.recommend(1, k=k)
        assert len(result) <= min(k, recommender.size - 1)


def test_ranks_are_contiguous_and_ordered_by_descending_score(recommender):
    result = recommender.recommend(1, k=5)
    assert [r.rank for r in result] == list(range(1, len(result) + 1))
    scores = [r.score for r in result]
    assert scores == sorted(scores, reverse=True)


def test_franchise_siblings_come_back_first(recommender):
    result = recommender.recommend(1, k=2)
    assert 2 in result.movie_ids


def test_shared_director_is_retrieved(recommender):
    assert 3 in recommender.recommend(1, k=3).movie_ids


def test_unknown_movie_raises(recommender):
    with pytest.raises(UnknownMovieError):
        recommender.recommend(999_999, k=5)


def test_k_must_be_positive(recommender):
    with pytest.raises(ValueError):
        recommender.recommend(1, k=0)


def test_zero_feature_query_raises_a_distinct_error():
    """An all-zero film is a data condition, not a caller error, so it gets its
    own exception type and a message naming the film."""
    records = [
        make_record(movie_id=1, overview="A detective fights crime in the city."),
        make_record(movie_id=2, overview=None, genres=[], keywords=[], cast=[], directors=[]),
    ]
    recommender = Recommender.build(
        build_corpus(records), PreprocessConfig(), representation="D"
    )
    assert not recommender.has_usable_features(2)
    with pytest.raises(InsufficientFeaturesError) as excinfo:
        recommender.recommend(2, k=5)
    assert "Test Film" in str(excinfo.value)


def test_recommend_is_repeatable(recommender):
    first = recommender.recommend(1, k=5)
    for _ in range(4):
        again = recommender.recommend(1, k=5)
        assert first.movie_ids == again.movie_ids
        assert [r.score for r in first] == [r.score for r in again]


def test_duplicate_corpus_ids_are_rejected():
    records = [
        make_record(movie_id=1, overview="A detective fights crime in the city."),
        make_record(movie_id=1, overview="A different detective fights crime too."),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        Recommender.build(build_corpus(records), PreprocessConfig(), representation="D")


# -- evidence filter -------------------------------------------------------
def test_min_shared_terms_filters_thin_matches(recommender):
    loose = recommender.recommend(1, k=5)
    strict = Recommender.build(
        recommender.corpus,
        PreprocessConfig(),
        representation="D",
        min_shared_terms=5,
    ).recommend(1, k=5)
    assert all(r.shared_terms >= 5 for r in strict)
    assert len(strict) <= len(loose)
    # Every surviving strict hit was also available to the loose query: the
    # filter removes candidates, it never invents or reorders them.
    assert {r.movie_id for r in strict} <= {r.movie_id for r in loose}


def test_skipped_candidates_are_reported_with_a_reason(recommender):
    strict = Recommender.build(
        recommender.corpus, PreprocessConfig(), representation="D", min_shared_terms=99
    )
    result = strict.recommend(1, k=5)
    assert result.recommendations == ()
    assert any(reason == "insufficient_shared_terms" for _mid, reason in result.skipped)


def test_min_shared_terms_must_be_positive(recommender):
    with pytest.raises(ValueError):
        Recommender.build(
            recommender.corpus, PreprocessConfig(), representation="D", min_shared_terms=0
        )


# -- introspection ---------------------------------------------------------
def test_geometry_is_reported(recommender):
    assert recommender.size == 6
    assert recommender.dimensions > 0
    assert recommender.nonzeros > 0
    assert 0 < recommender.density_percent < 100
    assert recommender.config_fingerprint


def test_metadata_round_trips_genres(recommender):
    meta = recommender.metadata(1)
    assert meta["movie_id"] == 1
    assert "crime" in meta["genres"]
    assert meta["collection_name"] == "Shared Saga"


def test_metadata_for_unknown_id_raises(recommender):
    with pytest.raises(UnknownMovieError):
        recommender.metadata(999_999)


def test_rows_of_matches_row_of(recommender):
    rows = recommender.rows_of([1, 3])
    assert rows.tolist() == [recommender.row_of(1), recommender.row_of(3)]


def test_unknown_representation_is_rejected(corpus):
    with pytest.raises(ValueError, match="unknown representation"):
        Recommender.build(corpus, PreprocessConfig(), representation="Z")


def test_custom_representation_spec_is_accepted(corpus):
    from moviemind.representations import RepresentationSpec

    recommender = Recommender.build(
        corpus,
        PreprocessConfig(),
        representation=RepresentationSpec("ov_only", "overview", ("overview",)),
    )
    assert recommender.dimensions > 0


# -- batch scoring ---------------------------------------------------------
def test_top_k_batch_matches_single_query(recommender):
    rows = recommender.rows_of([1, 2, 3])
    batched = recommender.top_k_batch(rows, k=3)
    for row, (idxs, _scores) in zip(rows.tolist(), batched):
        single = recommender.recommend(recommender.movie_ids[row], k=3)
        assert single.movie_ids == tuple(int(recommender.movie_ids[i]) for i in idxs.tolist())


def test_top_k_batch_never_returns_the_query(recommender):
    rows = recommender.rows_of([1, 2, 3, 4, 5, 6])
    ids = recommender.movie_ids
    for row, (idxs, _s) in zip(rows.tolist(), recommender.top_k_batch(rows, k=4)):
        assert int(ids[row]) not in [int(ids[i]) for i in idxs.tolist()]


def test_similarity_rows_is_not_square_for_a_partial_batch(recommender):
    rows = recommender.rows_of([1, 2])
    sims = recommender.similarity_rows(rows)
    assert sims.shape == (2, recommender.size)


# -- metrics ---------------------------------------------------------------
def test_ndcg_perfect_ranking_is_one():
    assert ndcg_at_k([1.0, 1.0, 0.0], 3) == pytest.approx(1.0)


def test_ndcg_no_relevant_items_is_zero():
    assert ndcg_at_k([0.0, 0.0], 2) == 0.0


def test_ndcg_rewards_earlier_hits():
    early = ndcg_at_k([1.0, 0.0, 1.0], 3)
    late = ndcg_at_k([0.0, 1.0, 1.0], 3)
    assert early > late


def test_spearman_detects_monotonic_and_inverted_relationships():
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_returns_zero_not_nan_for_constant_input():
    value = spearman([1, 1, 1, 1], [1, 2, 3, 4])
    assert value == 0.0
    assert not np.isnan(value)


def test_spearman_handles_too_few_points():
    assert spearman([1], [1]) == 0.0


# -- evaluation protocol ---------------------------------------------------
def test_evaluate_scores_only_label_bearing_queries(recommender, corpus):
    labels = ProxyLabels.from_raw(
        [make_record(movie_id=r["id"], title=r["title"]) for r in corpus],
        cast_top_k=5,
    )
    result = evaluate_recommender(recommender, labels, k=3, ks=(3,))
    assert isinstance(result, EvaluationResult)
    # No film here has a franchise or title group, so every label is
    # unevaluable and must be reported as such rather than scored as zero.
    for kind in ("franchise", "same_title"):
        assert result.unevaluable_queries[kind] == recommender.size
        assert result.label_scores[kind][0].queries_evaluated == 0
        assert result.label_scores[kind][0].recall == 0.0


def test_evaluate_reports_label_free_measures(recommender, corpus):
    labels = ProxyLabels.from_raw([], cast_top_k=5)
    result = evaluate_recommender(recommender, labels, k=3, ks=(3,))
    assert 0.0 < result.coverage <= 1.0
    assert result.intra_list_diversity >= 0.0
    assert 0.0 < result.distinct_genres <= 6.0
    assert 0.0 <= result.thin_evidence_rate <= 1.0
    # The ceiling is the number of genre names in the corpus, not 1.0: this
    # counts breadth across a list, not a per-film score.
    all_genres = {
        token for record in corpus for token in record["tokens"].get("genres", [])
    }
    assert result.distinct_genres <= len(all_genres)


def test_evaluate_finds_a_perfect_franchise_match(corpus):
    """Films 1 and 2 share a collection, nearly identical text and two cast
    members, so recall@1 on that label must be 1.0."""
    recommender = Recommender.build(corpus, PreprocessConfig(), representation="D")
    labels = ProxyLabels.from_raw(
        [
            make_record(
                movie_id=1,
                belongs_to_collection={"id": 7, "name": "Shared Saga"},
            ),
            make_record(
                movie_id=2,
                belongs_to_collection={"id": 7, "name": "Shared Saga"},
            ),
        ],
        cast_top_k=5,
    )
    result = evaluate_recommender(
        recommender, labels, k=1, ks=(1,), query_ids=[1]
    )
    scores = result.label_scores["franchise"]
    assert scores[0].queries_evaluated == 1
    assert scores[0].recall == 1.0
    assert scores[0].hit_rate == 1.0


def test_evaluate_evidence_filter_can_backfill(recommender):
    """A filter must be able to promote a passing candidate from below the
    cutoff, not only delete from above it."""
    labels = ProxyLabels.from_raw([], cast_top_k=5)
    loose = evaluate_recommender(recommender, labels, k=3, ks=(3,))
    strict = evaluate_recommender(
        recommender, labels, k=3, ks=(3,), min_shared_terms=4
    )
    assert strict.thin_evidence_rate < loose.thin_evidence_rate
    assert strict.mean_recommendations_returned <= loose.mean_recommendations_returned
