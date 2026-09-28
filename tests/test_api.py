"""
API test suite.

**Hermetic by construction.** No test in this file opens a socket, reads
``.env``, or needs a TMDb token. The catalogue is either a small controlled
corpus built inline, or the local Phase 3 artifact behind an explicit
``skipif``. That is a hard requirement, not a convenience: a suite that needs a
network or a credential stops being runnable by anyone who lacks the secret, and
stops being trustworthy when it fails for reasons unrelated to the code.

The controlled corpus is the important one. The real 5,000-film snapshot cannot
produce the conditions that matter most here -- a film with an all-zero feature
vector, or a catalogue with fewer valid candidates than the requested ``k``.
Those are exactly the states the error and backfill contracts live in, so they
are built on purpose.
"""

from __future__ import annotations

import sys
from dataclasses import replace as dc_replace
from pathlib import Path
from typing import get_args

import pytest
from fastapi.testclient import TestClient

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import PROCESSED_CORPUS, make_record  # noqa: E402

from moviemind.api.app import API_PREFIX, create_app  # noqa: E402
from moviemind.api.service import CatalogueLoadError, build_service  # noqa: E402
from moviemind.api.settings import Settings  # noqa: E402
from moviemind.config import PreprocessConfig  # noqa: E402
from moviemind.experiments import SELECTED_GENRE_WEIGHT  # noqa: E402
from moviemind.pipeline import build_movie_features  # noqa: E402

#: A known-good id in the 5,000-film snapshot, used by the integration tests.
#: Picked because it is a popular, well-populated film whose neighbourhood is
#: non-trivial, so a recommendation request returns a full list.
REAL_MOVIE_ID = 278  # The Shawshank Redemption

#: An id that is well-formed but absent from every catalogue used here.
ABSENT_MOVIE_ID = 999_999_99

V1 = f"{API_PREFIX}"


# ---------------------------------------------------------------------------
# Controlled corpus
# ---------------------------------------------------------------------------
# Thirteen films in three thematic clusters, so a query for one cluster returns
# same-cluster films. Descriptions deliberately share vocabulary within a cluster
# and little across clusters; genres reinforce the split. Because the corpora
# are tiny, `k=5` cannot always be filled -- which is the point: the exhausted
# branch of the backfill contract is reachable here and is not reachable on the
# real snapshot at k=10.
CLUSTER_SPACE = [
    "orbital station",
    "space station",
    "astronaut",
    "spaceship",
    "lunar mission",
]
CLUSTER_OCEAN = [
    "submarine",
    "deep ocean",
    "naval vessel",
    "sailor",
    "undersea",
]
CLUSTER_HEIST = [
    "bank robbery",
    "safecracker",
    "getaway car",
    "crew",
    "vault",
]

CONTROLLED_RAW = [
    make_record(movie_id=100, title="Orbital Station", overview=(
        "An astronaut aboard a damaged space station must reach the lunar mission "
        "module before the orbital hull fails, with a spaceship crew racing alongside."
    ), genres=["Science Fiction"], keywords=CLUSTER_SPACE, cast=["Grace Hopper"],
       directors=["Kip Thorne"]),
    make_record(movie_id=101, title="Deep Ocean", overview=(
        "A submarine crew descends toward a deep ocean trench where a naval vessel "
        "has sunk, and an undersea search becomes a fight for air and a sailor alive."
    ), genres=["Drama"], keywords=CLUSTER_OCEAN, cast=["Jacques Cousteau"],
       directors=["Robert Ballard"]),
    make_record(movie_id=102, title="The Getaway", overview=(
        "A bank robbery crew hires a safecracker to open the vault while a getaway "
        "car waits, and the crew argue over the split as the police close in."
    ), genres=["Crime"], keywords=CLUSTER_HEIST, cast=["George Miller"],
       directors=["Steven Soderbergh"]),
    make_record(movie_id=103, title="Lunar Drift", overview=(
        "A second orbital station crew attempts a lunar mission launch, and the "
        "spaceship inherits damage from the first station's failing orbital hull."
    ), genres=["Science Fiction"], keywords=CLUSTER_SPACE, cast=["Sally Ride"],
       directors=["Kip Thorne"]),
    make_record(movie_id=104, title="Trench", overview=(
        "A deep ocean documentary follows an undersea descent, and a naval vessel "
        "submarine loses power while an astronaut trains for a mission above."
    ), genres=["Documentary"], keywords=CLUSTER_OCEAN, cast=["Don Walsh"],
       directors=["James Cameron"]),
    make_record(movie_id=105, title="Vault Job", overview=(
        "A second bank robbery turns on a getaway car, a safecracker and a crew "
        "that has done this vault job before."
    ), genres=["Crime"], keywords=CLUSTER_HEIST, cast=["George Miller"],
       directors=["Steven Soderbergh"]),
    make_record(movie_id=106, title="Station Keepers", overview=(
        "Two astronauts maintain an orbital station, and a lunar mission resupply "
        "becomes routine until a spaceship reports damage to the station hull."
    ), genres=["Science Fiction"], keywords=CLUSTER_SPACE, cast=["Sally Ride"],
       directors=["Kip Thorne"]),
    make_record(movie_id=107, title="Silent Sea", overview=(
        "A submarine in the deep ocean loses its undersea charts, and the sailor "
        "crew must reach a naval vessel before the trench claims the vessel."
    ), genres=["Thriller"], keywords=CLUSTER_OCEAN, cast=["Don Walsh"],
       directors=["James Cameron"]),
    make_record(movie_id=108, title="Crew Change", overview=(
        "The getaway crew changes one member before the bank robbery, and the "
        "new safecracker has plans for the vault and the getaway car."
    ), genres=["Crime"], keywords=CLUSTER_HEIST, cast=["George Miller"],
       directors=["Steven Soderbergh"]),
    make_record(movie_id=109, title="Orbital Drift", overview=(
        "An independent film about an astronaut, a space station and a lunar "
        "mission, sharing vocabulary with several other station films."
    ), genres=["Science Fiction"], keywords=CLUSTER_SPACE, cast=["Grace Hopper"],
       directors=["Kip Thorne"]),
    make_record(movie_id=110, title="Abyss Crew", overview=(
        "An undersea thriller in which a submarine crew crosses a deep ocean "
        "trench while a naval vessel convoy travels above the water."
    ), genres=["Action"], keywords=CLUSTER_OCEAN, cast=["Jacques Cousteau"],
       directors=["Robert Ballard"]),
    make_record(movie_id=111, title="Split", overview=(
        "A getaway car splits a bank robbery crew in two, and a safecracker is "
        "left holding the vault key with no vehicle and no plan."
    ), genres=["Crime"], keywords=CLUSTER_HEIST, cast=["George Miller"],
       directors=["Steven Soderbergh"]),
    # No tokens whatsoever: the all-zero-vector case, and the only way to reach
    # the engine's InsufficientFeaturesError. Every text field is explicitly
    # empty, so the Phase 3 pipeline yields a record with empty blocks.
    # `document_size` is 0 here, whereas all 5,000 real films have it > 0.
    make_record(movie_id=900, title="Untitled Project", overview=None,
                genres=[], keywords=[], cast=[], directors=[]),
]

#: A film in the space cluster, used as the recommendation query.
SPACE_QUERY_ID = 100
#: The all-zero-vector film.
NO_FEATURES_ID = 900
#: A film whose valid-neighbour set is small, so `k=5` cannot be filled.
#: Verified by test_exhausted_reports_short_list_honestly below.
SMALL_NEIGHBOURHOOD_ID = 102


def _controlled_corpus() -> list[dict]:
    """
    Build the Phase 3 processed corpus for the controlled fixtures.

    Uses ``build_movie_features`` per record rather than ``build_corpus`` on a
    temp file: it is the same function ``build_corpus`` loops over, so the
    fixture exercises real tokenisation and normalisation, and it needs no
    filesystem. The ``None`` branch drops records without a usable TMDb id,
    which these fixtures never produce.
    """
    cfg = PreprocessConfig()
    cfg = dc_replace(cfg, genres=dc_replace(cfg.genres, weight=SELECTED_GENRE_WEIGHT))
    records = [build_movie_features(record, cfg) for record in CONTROLLED_RAW]
    return [record for record in records if record is not None]


@pytest.fixture(scope="module")
def controlled_service():
    """A CatalogueService over the controlled corpus. Built once per module."""
    return build_service(_controlled_corpus(), Settings())


@pytest.fixture
def client(controlled_service) -> TestClient:
    """A TestClient wired to the controlled catalogue, lifespan included."""
    with TestClient(create_app(Settings(), service=controlled_service)) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def real_client():
    """
    A TestClient over the real 5,000-film snapshot.

    Skipped when the processed corpus is absent, which is the normal case for
    anyone who has not run the Phase 3 build. Nothing here rebuilds features:
    the corpus is read as-is.
    """
    if not PROCESSED_CORPUS.exists():
        pytest.skip("processed corpus absent; run scripts/build_features.py")
    with TestClient(create_app(Settings())) as test_client:
        yield test_client


@pytest.fixture
def tight_settings() -> Settings:
    """Settings with deliberately tiny ceilings, so limits test hermetically."""
    return Settings(
        max_k=3,
        default_k=2,
        max_search_limit=4,
        default_search_limit=2,
        max_query_length=12,
    )


@pytest.fixture
def tight_client(tight_settings) -> TestClient:
    """
    A client with deliberately tiny ceilings.

    The service is built *from* ``tight_settings`` rather than reusing the shared
    fixture, because the service is what enforces the ceilings: the limits a
    client observes must be the limits the service was configured with.
    """
    tight_service = build_service(_controlled_corpus(), tight_settings)
    with TestClient(create_app(tight_settings, service=tight_service)) as test_client:
        yield test_client


# ---------------------------------------------------------------------------
# 1. Health / readiness
# ---------------------------------------------------------------------------
class TestHealth:
    def test_health_is_200_and_reports_the_version(self, client):
        body = client.get(f"{V1}/health").json()
        assert body == {"status": "ok", "service": "moviemind-api", "api_version": "v1"}

    def test_health_does_not_require_the_catalogue(self, client):
        """Liveness must not depend on data loading, or a slow index looks dead."""
        assert client.get(f"{V1}/health").status_code == 200

    def test_ready_reports_catalogue_stats(self, client):
        response = client.get(f"{V1}/ready")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "ready"
        assert body["catalogue"]["films"] == len(CONTROLLED_RAW)
        assert body["catalogue"]["dimensions"] > 0
        assert body["catalogue"]["load_seconds"] >= 0

    def test_ready_503_when_catalogue_missing(self, tmp_path):
        """A missing corpus must surface as unready, not as a boot crash."""
        settings = Settings(corpus_path=tmp_path / "does_not_exist.jsonl")
        with TestClient(create_app(settings)) as broken:
            assert broken.get(f"{V1}/health").status_code == 200
            ready = broken.get(f"{V1}/ready")
            assert ready.status_code == 503
            assert ready.json()["status"] == "not_ready"
            assert "does_not_exist.jsonl" in ready.json()["reason"]
            # The reason names the file and the fix, and leaks no traceback.
            assert "build_features.py" in ready.json()["reason"]
            assert "Traceback" not in ready.json()["reason"]

    def test_data_routes_are_503_when_catalogue_missing(self, tmp_path):
        settings = Settings(corpus_path=tmp_path / "nope.jsonl")
        with TestClient(create_app(settings)) as broken:
            for path in (
                f"{V1}/movies/{REAL_MOVIE_ID}",
                f"{V1}/movies/{REAL_MOVIE_ID}/recommendations",
                f"{V1}/movies/search?q=star",
                f"{V1}/meta",
            ):
                response = broken.get(path)
                assert response.status_code == 503, path
                assert response.json()["error"]["code"] == "catalogue_unavailable"

    def test_empty_corpus_file_is_reported_clearly(self, tmp_path):
        empty = tmp_path / "movies.jsonl"
        empty.write_text("", encoding="utf-8")
        with TestClient(create_app(Settings(corpus_path=empty))) as broken:
            ready = broken.get(f"{V1}/ready")
            assert ready.status_code == 503
            assert "empty" in ready.json()["reason"]

    def test_load_error_mentions_the_remedy(self, tmp_path):
        from moviemind.api.service import CatalogueService

        with pytest.raises(CatalogueLoadError, match="build_features.py"):
            CatalogueService.load(Settings(corpus_path=tmp_path / "absent.jsonl"))


# ---------------------------------------------------------------------------
# 2. Search
# ---------------------------------------------------------------------------
class TestSearch:
    def test_search_finds_a_title(self, client):
        response = client.get(f"{V1}/movies/search", params={"q": "orbital"})
        assert response.status_code == 200
        body = response.json()
        assert body["count"] == len(body["results"]) >= 1
        assert {r["movie_id"] for r in body["results"]} == {100, 109}

    def test_search_is_case_insensitive(self, client):
        """Matching is case-folded; the echoed `query` is what the user typed."""
        def results(value):
            return client.get(f"{V1}/movies/search", params={"q": value}).json()["results"]

        plain = results("orbital")
        assert plain, "fixture should match"
        assert results("ORBITAL") == plain
        assert results("OrBiTaL") == plain

    def test_search_echoes_the_trimmed_query(self, client):
        assert client.get(f"{V1}/movies/search", params={"q": "  orbital "}).json()["query"] == "orbital"

    def test_search_is_whitespace_insensitive(self, client):
        padded = client.get(f"{V1}/movies/search", params={"q": "  orbital  "})
        plain = client.get(f"{V1}/movies/search", params={"q": "orbital"})
        assert padded.json() == plain.json()

    def test_search_is_deterministic(self, client):
        first = client.get(f"{V1}/movies/search", params={"q": "o"}).json()
        for _ in range(4):
            assert client.get(f"{V1}/movies/search", params={"q": "o"}).json() == first

    def test_empty_query_is_400_not_an_empty_result(self, client):
        """A malformed query and a search that matched nothing are different."""
        for value in ("", "   ", "\t\n"):
            response = client.get(f"{V1}/movies/search", params={"q": value})
            assert response.status_code == 400, repr(value)
            assert response.json()["error"]["code"] == "empty_query"

    def test_missing_query_is_422(self, client):
        response = client.get(f"{V1}/movies/search")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_no_results_is_200_with_an_empty_list(self, client):
        response = client.get(f"{V1}/movies/search", params={"q": "zzzznotafilm"})
        assert response.status_code == 200
        body = response.json()
        assert body["results"] == []
        assert body["count"] == 0
        assert body["total_matches"] == 0

    def test_total_matches_exceeds_the_returned_count(self, client):
        body = client.get(f"{V1}/movies/search", params={"q": "o", "limit": 1}).json()
        assert body["count"] == 1
        assert body["total_matches"] > 1
        assert body["limit"] == 1

    def test_limit_defaults_and_is_capped(self, tight_client):
        default = tight_client.get(f"{V1}/movies/search", params={"q": "o"}).json()
        assert default["limit"] == 2
        assert default["count"] <= 2
        capped = tight_client.get(f"{V1}/movies/search", params={"q": "o", "limit": 4}).json()
        assert capped["limit"] == 4

    @pytest.mark.parametrize("limit", [0, -1, 5, 9999])
    def test_invalid_limit_is_422(self, tight_client, limit):
        response = tight_client.get(f"{V1}/movies/search", params={"q": "o", "limit": limit})
        assert response.status_code == 422
        body = response.json()["error"]
        assert body["code"] == "invalid_limit"
        assert body["details"]["max_limit"] == 4

    def test_query_too_long_is_400(self, tight_client):
        response = tight_client.get(f"{V1}/movies/search", params={"q": "x" * 13})
        assert response.status_code == 400
        body = response.json()["error"]
        assert body["code"] == "query_too_long"
        assert body["details"]["max_query_length"] == 12

    def test_exact_match_outranks_a_substring(self, client):
        """Tier 0 (exact) must precede tier 3 (contains) in the response."""
        body = client.get(f"{V1}/movies/search", params={"q": "trench"}).json()
        kinds = [r["match"] for r in body["results"]]
        assert "exact" in kinds
        assert kinds.index("exact") == 0

    def test_results_carry_useful_metadata(self, client):
        hit = client.get(f"{V1}/movies/search", params={"q": "orbital"}).json()["results"][0]
        assert set(hit) == {
            "movie_id", "title", "original_title", "release_year",
            "genres", "popularity", "match",
        }
        assert isinstance(hit["movie_id"], int)
        assert isinstance(hit["title"], str)
        assert isinstance(hit["genres"], list)

    def test_results_expose_no_internal_representation(self, client):
        text = client.get(f"{V1}/movies/search", params={"q": "orbital"}).text
        for leaked in ("tokens", "document", "gn:", "ov:", "cast:", "dir:"):
            assert leaked not in text, leaked

    def test_response_size_is_bounded(self, client):
        """A one-character query matches a lot; the response must still be small."""
        body = client.get(f"{V1}/movies/search", params={"q": "a"}).json()
        assert body["count"] <= 10
        assert body["limit"] == 10


# ---------------------------------------------------------------------------
# 3. Movie details
# ---------------------------------------------------------------------------
class TestDetails:
    def test_details_success(self, client):
        response = client.get(f"{V1}/movies/{SPACE_QUERY_ID}")
        assert response.status_code == 200
        body = response.json()
        assert body["movie_id"] == SPACE_QUERY_ID
        assert body["title"] == "Orbital Station"
        assert body["genres"]
        assert body["recommendable"] is True
        assert body["document_size"] > 0

    def test_details_includes_every_documented_field(self, client):
        body = client.get(f"{V1}/movies/{SPACE_QUERY_ID}").json()
        assert set(body) == {
            "movie_id", "title", "original_title", "release_year", "release_date",
            "runtime_minutes", "original_language", "genres", "collection_name",
            "popularity", "vote_average", "vote_count", "adult",
            "feature_counts", "document_size", "recommendable",
        }

    def test_details_never_exposes_raw_representation(self, client):
        """`document` and `tokens` are internal; only counts may cross."""
        body = client.get(f"{V1}/movies/{SPACE_QUERY_ID}").json()
        assert "document" not in body
        assert "tokens" not in body
        # feature_counts must be counts, not the vocabulary itself.
        assert all(isinstance(v, int) for v in body["feature_counts"].values())

    def test_unknown_movie_is_404(self, client):
        response = client.get(f"{V1}/movies/{ABSENT_MOVIE_ID}")
        assert response.status_code == 404
        body = response.json()["error"]
        assert body["code"] == "unknown_movie"
        assert body["details"]["movie_id"] == ABSENT_MOVIE_ID

    @pytest.mark.parametrize("bad", ["abc", "12.5", "1e3", "%20", "null"])
    def test_non_integer_movie_id_is_422(self, client, bad):
        response = client.get(f"{V1}/movies/{bad}")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_negative_movie_id_is_404_not_500(self, client):
        response = client.get(f"{V1}/movies/-1")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "unknown_movie"

    def test_film_without_features_is_flagged_not_fatal(self, client):
        """The UI needs to know in advance, so details must not raise."""
        body = client.get(f"{V1}/movies/{NO_FEATURES_ID}").json()
        assert body["recommendable"] is False
        assert body["document_size"] == 0


# ---------------------------------------------------------------------------
# 4. Recommendations
# ---------------------------------------------------------------------------
class TestRecommendations:
    def test_recommendations_success(self, client):
        response = client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations")
        assert response.status_code == 200
        body = response.json()
        assert body["query"] == {"movie_id": SPACE_QUERY_ID, "title": "Orbital Station"}
        assert body["k"] == 10
        assert body["similarity"] == "cosine"
        assert 1 <= body["evidence"]["returned"] <= body["k"]

    def test_default_k_is_ten(self, client):
        assert client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations").json()["k"] == 10

    def test_recommendations_are_relevant_to_the_query(self, client):
        """Content-based, not arbitrary: same-cluster films should dominate."""
        body = client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 5}).json()
        assert body["recommendations"], "expected at least one neighbour"
        # 103, 106, 109 are the other space-cluster films; 107 shares only the
        # word "submarine"-adjacent overlap and is the known weak match.
        assert {r["movie_id"] for r in body["recommendations"]} & {103, 106, 109}

    def test_query_movie_is_excluded(self, client):
        body = client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 10}).json()
        assert SPACE_QUERY_ID not in {r["movie_id"] for r in body["recommendations"]}

    def test_no_duplicate_movies(self, client):
        for k in (1, 3, 5, 10):
            body = client.get(
                f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": k}
            ).json()
            ids = [r["movie_id"] for r in body["recommendations"]]
            assert len(ids) == len(set(ids)), k

    def test_never_returns_more_than_k(self, client):
        for k in (1, 2, 3, 5, 8, 10):
            body = client.get(
                f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": k}
            ).json()
            assert len(body["recommendations"]) <= k, k

    def test_ranks_are_contiguous_from_one(self, client):
        body = client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations").json()
        ranks = [r["rank"] for r in body["recommendations"]]
        assert ranks == list(range(1, len(ranks) + 1))

    def test_scores_are_non_increasing(self, client):
        body = client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations").json()
        scores = [r["score"] for r in body["recommendations"]]
        assert scores == sorted(scores, reverse=True)

    def test_scores_are_cosine_and_bounded(self, client):
        body = client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations").json()
        for item in body["recommendations"]:
            assert -1.0 <= item["score"] <= 1.0

    def test_ordering_is_deterministic(self, client):
        path = f"{V1}/movies/{SPACE_QUERY_ID}/recommendations"
        first = client.get(path, params={"k": 5}).json()
        for _ in range(5):
            assert client.get(path, params={"k": 5}).json() == first

    def test_ordering_is_deterministic_across_app_instances(self, controlled_service):
        """A fresh app must agree byte-for-byte; ties break on id, not position."""
        path = f"{V1}/movies/{SPACE_QUERY_ID}/recommendations"
        with TestClient(create_app(service=controlled_service)) as a, TestClient(
            create_app(service=controlled_service)
        ) as b:
            assert a.get(path, params={"k": 5}).json() == b.get(path, params={"k": 5}).json()

    def test_evidence_filter_is_preserved(self, client):
        """min_shared_terms=3 must survive the API boundary."""
        body = client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations").json()
        assert body["evidence"]["min_shared_terms"] == 3
        for item in body["recommendations"]:
            assert item["shared_terms"] >= 3

    def test_recommendation_items_carry_metadata(self, client):
        item = client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 3}
        ).json()["recommendations"][0]
        assert set(item) == {
            "rank", "movie_id", "title", "score", "year", "genres", "popularity",
            "vote_average", "vote_count", "collection_name", "shared_terms",
        }
        assert item["title"]
        assert isinstance(item["shared_terms"], int)

    def test_backfill_is_observable_through_the_evidence_block(self, client):
        """The Phase 4 backfill contract must be visible to a client."""
        body = client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 10}).json()
        evidence = body["evidence"]
        assert evidence["candidates_rejected"] > 0, "fixture should force rejections"
        assert evidence["candidates_examined"] == (
            evidence["returned"] + evidence["candidates_rejected"]
        )
        # Backfilled: more candidates examined than results returned.
        assert evidence["candidates_examined"] > evidence["returned"]

    def test_skipped_candidates_explain_the_rejections(self, client):
        body = client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations").json()
        skipped = body["skipped"]
        assert len(skipped) == body["evidence"]["candidates_rejected"]
        for entry in skipped:
            assert entry["reason"] in {"below_min_score", "insufficient_shared_terms"}
            assert isinstance(entry["movie_id"], int)

    def test_skip_vocabulary_matches_the_engine(self):
        """
        The evidence vocabulary is pinned to what the engine actually emits.

        The service narrows the engine's `tuple[int, str]` through a table, and an
        unrecognised reason raises rather than being relabelled. This test is what
        tells us the table has drifted, instead of a request discovering it.
        """
        from moviemind.api.schemas import SkipReason
        from moviemind.api.service import SKIP_REASONS

        assert set(SKIP_REASONS) == {"below_min_score", "insufficient_shared_terms"}
        assert set(SKIP_REASONS.values()) == set(get_args(SkipReason))
        # The engine's own literals, read from source, are the ground truth.
        engine_source = Path(REPO_ROOT / "moviemind" / "recommend.py").read_text(encoding="utf-8")
        for reason in SKIP_REASONS:
            assert f'"{reason}")' in engine_source, reason

    def test_exhausted_reports_short_list_honestly(self, client):
        """Fewer than k means the catalogue ran out -- and says so."""
        body = client.get(
            f"{V1}/movies/{SMALL_NEIGHBOURHOOD_ID}/recommendations", params={"k": 10}
        ).json()
        assert len(body["recommendations"]) < 10
        assert body["evidence"]["exhausted"] is True
        assert body["evidence"]["returned"] == len(body["recommendations"])

    def test_exhausted_is_false_when_the_list_fills(self, client):
        body = client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 3}
        ).json()
        assert len(body["recommendations"]) == 3
        assert body["evidence"]["exhausted"] is False

    def test_unknown_movie_is_404(self, client):
        response = client.get(f"{V1}/movies/{ABSENT_MOVIE_ID}/recommendations")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "unknown_movie"

    def test_film_without_features_is_422(self, client):
        """A data condition, not a caller error, and not a 404."""
        response = client.get(f"{V1}/movies/{NO_FEATURES_ID}/recommendations")
        assert response.status_code == 422
        body = response.json()["error"]
        assert body["code"] == "insufficient_features"
        assert body["details"]["reason"] == "all_zero_feature_vector"

    @pytest.mark.parametrize("k", [0, -1, 51, 100000])
    def test_invalid_k_is_422(self, client, k):
        response = client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": k}
        )
        assert response.status_code == 422
        body = response.json()["error"]
        assert body["code"] == "invalid_k"
        assert body["details"]["max_k"] == 50

    def test_non_integer_k_is_422(self, client):
        response = client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": "ten"}
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    @pytest.mark.parametrize("k", [1, 2, 25, 50])
    def test_k_boundaries_are_accepted(self, client, k):
        response = client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": k}
        )
        assert response.status_code == 200
        assert response.json()["k"] == k

    def test_k_ceiling_is_configurable(self, tight_client):
        assert tight_client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 3}
        ).status_code == 200
        assert tight_client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 4}
        ).status_code == 422

    def test_smaller_k_is_a_prefix_of_larger_k(self, client):
        """Filtering is order-preserving, so k=3 must lead k=5."""
        short = client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 3}
        ).json()["recommendations"]
        long = client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 5}
        ).json()["recommendations"]
        assert [r["movie_id"] for r in short] == [r["movie_id"] for r in long[: len(short)]]


# ---------------------------------------------------------------------------
# 5. Error format, routing, CORS
# ---------------------------------------------------------------------------
class TestErrorFormat:
    def test_every_error_uses_one_envelope(self, client):
        cases = [
            (f"{V1}/movies/{ABSENT_MOVIE_ID}", 404),
            (f"{V1}/movies/abc", 422),
            (f"{V1}/movies/search", 422),
            (f"{V1}/movies/search?q=", 400),
            (f"{V1}/movies/{SPACE_QUERY_ID}/recommendations?k=0", 422),
            (f"{V1}/does-not-exist", 404),
        ]
        for path, expected in cases:
            response = client.get(path)
            assert response.status_code == expected, path
            body = response.json()
            assert set(body) == {"error"}, path
            assert set(body["error"]) <= {"code", "message", "status", "details"}, path
            assert body["error"]["status"] == expected, path
            assert isinstance(body["error"]["code"], str) and body["error"]["code"]
            assert isinstance(body["error"]["message"], str) and body["error"]["message"]

    def test_unknown_route_uses_the_envelope(self, client):
        response = client.get(f"{V1}/nope")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"

    def test_wrong_method_uses_the_envelope(self, client):
        response = client.post(f"{V1}/health")
        assert response.status_code == 405
        assert response.json()["error"]["code"] == "method_not_allowed"

    def test_errors_never_leak_internals(self, client):
        for path in (
            f"{V1}/movies/{ABSENT_MOVIE_ID}",
            f"{V1}/movies/{NO_FEATURES_ID}/recommendations",
            f"{V1}/movies/abc",
            f"{V1}/movies/search?q=",
        ):
            text = client.get(path).text
            for leak in ("Traceback", "File \"", "moviemind/", "site-packages", "line "):
                assert leak not in text, (path, leak)

    def test_unexpected_error_is_not_leaked(self, controlled_service, monkeypatch):
        """An unhandled exception must return a fixed body, not its message."""
        from moviemind.api import service as service_module

        def boom(*_args, **_kwargs):
            raise RuntimeError("SECRET-INTERNAL-DETAIL /etc/passwd")

        monkeypatch.setattr(service_module.CatalogueService, "recommend", boom)
        # raise_server_exceptions=False is what a real server does: the handler
        # runs and the process survives. Left at the TestClient default, the
        # exception is re-raised into the test instead of becoming a response.
        with TestClient(
            create_app(Settings(), service=controlled_service),
            raise_server_exceptions=False,
        ) as broken:
            response = broken.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations")
        assert response.status_code == 500
        body = response.json()["error"]
        assert body["code"] == "internal_error"
        assert body["status"] == 500
        assert "SECRET-INTERNAL-DETAIL" not in response.text
        assert "/etc/passwd" not in response.text
        assert "Traceback" not in response.text

    def test_error_codes_are_documented_constants(self):
        from moviemind.api import errors

        for name in (
            "CODE_EMPTY_QUERY", "CODE_QUERY_TOO_LONG", "CODE_INVALID_K",
            "CODE_INVALID_LIMIT", "CODE_UNKNOWN_MOVIE", "CODE_INSUFFICIENT_FEATURES",
            "CODE_VALIDATION_ERROR", "CODE_CATALOGUE_UNAVAILABLE",
            "CODE_INTERNAL_ERROR", "CODE_NOT_FOUND", "CODE_METHOD_NOT_ALLOWED",
        ):
            assert isinstance(getattr(errors, name), str)


class TestRouting:
    def test_everything_is_versioned(self, client):
        """No endpoint may sit outside /api/v1."""
        assert client.get("/health").status_code == 404
        assert client.get("/movies/search?q=orbital").status_code == 404
        assert client.get(f"{V1}/health").status_code == 200

    def test_search_route_is_not_captured_by_movie_id(self, client):
        """`/movies/search` must not be parsed as a movie id."""
        assert client.get(f"{V1}/movies/search?q=orbital").status_code == 200

    def test_openapi_is_served(self, client):
        spec = client.get("/openapi.json").json()
        paths = spec["paths"]
        assert f"{V1}/health" in paths
        assert f"{V1}/movies/search" in paths
        assert f"{V1}/movies/{{movie_id}}" in paths
        assert f"{V1}/movies/{{movie_id}}/recommendations" in paths

    def test_duration_header_is_present(self, client):
        assert "x-request-duration-ms" in client.get(f"{V1}/health").headers


class TestCors:
    def test_allowed_origin_gets_cors_headers(self, client):
        response = client.get(
            f"{V1}/health", headers={"Origin": "http://localhost:5173"}
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "http://localhost:5173"

    def test_second_default_origin_is_allowed(self, client):
        response = client.get(
            f"{V1}/health", headers={"Origin": "http://127.0.0.1:3000"}
        )
        assert response.headers["access-control-allow-origin"] == "http://127.0.0.1:3000"

    def test_foreign_origin_is_not_granted(self, client):
        """No wildcard, no reflection: an unknown origin gets no CORS grant."""
        for origin in ("https://evil.example.com", "http://localhost:9999", "null"):
            response = client.get(f"{V1}/health", headers={"Origin": origin})
            assert "access-control-allow-origin" not in response.headers, origin

    def test_wildcard_is_never_the_default(self):
        assert "*" not in Settings().cors_origin_list

    def test_preflight_allows_get_only(self, client):
        response = client.options(
            f"{V1}/health",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert "post" not in response.headers.get("access-control-allow-methods", "").lower()

    def test_credentials_are_not_allowed(self, client):
        response = client.get(
            f"{V1}/health", headers={"Origin": "http://localhost:5173"}
        )
        assert "access-control-allow-credentials" not in response.headers

    def test_origins_are_configurable(self):
        settings = Settings(cors_origins="http://a.test, http://b.test/ ,http://a.test")
        assert settings.cors_origin_list == ["http://a.test", "http://b.test"]

    def test_configured_origin_is_honoured(self, controlled_service):
        settings = Settings(cors_origins="http://allowed.test")
        with TestClient(create_app(settings, service=controlled_service)) as scoped:
            good = scoped.get(f"{V1}/health", headers={"Origin": "http://allowed.test"})
            assert good.headers["access-control-allow-origin"] == "http://allowed.test"
            bad = scoped.get(f"{V1}/health", headers={"Origin": "http://localhost:5173"})
            assert "access-control-allow-origin" not in bad.headers


class TestMeta:
    def test_meta_publishes_the_locked_configuration(self, client):
        body = client.get(f"{V1}/meta").json()
        assert body["representation"] == "D"
        assert body["min_shared_terms"] == 3
        assert body["similarity"] == "cosine"
        assert body["config_fingerprint"].startswith("af683a71")

    def test_meta_fingerprint_matches_phase_4(self, client):
        """Guards against the API silently drifting from the measured config."""
        from dataclasses import replace as r

        from moviemind.experiments import SELECTED_MIN_SHARED_TERMS

        base = PreprocessConfig()
        expected = r(base, genres=r(base.genres, weight=SELECTED_GENRE_WEIGHT)).fingerprint()
        assert client.get(f"{V1}/meta").json()["config_fingerprint"] == expected
        assert SELECTED_MIN_SHARED_TERMS == 3

    def test_meta_carries_tmdb_attribution(self, client):
        attribution = client.get(f"{V1}/meta").json()["attribution"]
        assert "TMDB" in attribution["notice"]
        assert "not endorsed" in attribution["notice"]
        assert attribution["source"] == "themoviedb.org"

    def test_tmdb_notice_is_verbatim(self, client):
        """
        The licence requires this exact sentence, not a paraphrase.

        Pinned character-for-character against docs/phase-01-dataset-strategy.md
        sec 7.1, with the bracketed placeholder resolved to "product". The
        trailing comma after "certified" is part of the required wording.
        """
        from moviemind.api.service import TMDB_NOTICE

        assert TMDB_NOTICE == (
            "This product uses TMDB and the TMDB APIs but is not endorsed, "
            "certified, or otherwise approved by TMDB."
        )
        assert client.get(f"{V1}/meta").json()["attribution"]["notice"] == TMDB_NOTICE

    def test_meta_publishes_limits(self, client):
        limits = client.get(f"{V1}/meta").json()["limits"]
        assert limits == {
            "default_k": 10, "max_k": 50,
            "default_search_limit": 10, "max_search_limit": 50,
            "max_query_length": 200,
        }

    def test_published_limits_are_the_limits_actually_enforced(self, tight_client):
        """
        Guards a real trap: the service enforces ceilings, the app publishes them.

        If they were configured from different objects, /meta would advertise a
        max_k the routes do not honour. Comparing the two over the wire is the
        only way to catch that.
        """
        limits = tight_client.get(f"{V1}/meta").json()["limits"]
        assert limits["max_k"] == 3
        assert tight_client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 3}
        ).status_code == 200
        rejected = tight_client.get(
            f"{V1}/movies/{SPACE_QUERY_ID}/recommendations", params={"k": 4}
        )
        assert rejected.status_code == 422
        assert rejected.json()["error"]["details"]["max_k"] == limits["max_k"]

    def test_injected_service_supplies_the_settings(self, controlled_service):
        """create_app must not fall back to defaults when a service is injected."""
        with TestClient(create_app(service=controlled_service)) as derived:
            assert derived.get(f"{V1}/meta").json()["limits"]["max_k"] == 50

    def test_meta_carries_the_measured_caveats(self, client):
        notes = " ".join(client.get(f"{V1}/meta").json()["notes"])
        assert "no relevance ground truth" in notes.lower()
        assert "thin-evidence" in notes


# ---------------------------------------------------------------------------
# 6. Security / privacy
# ---------------------------------------------------------------------------
class TestSecurityAndPrivacy:
    def test_no_tmdb_credential_is_needed(self, monkeypatch, controlled_service):
        """The API must run with no TMDb credential in the environment at all."""
        for key in ("TMDB_API_READ_ACCESS_TOKEN", "TMDB_API_KEY", "TMDB_TOKEN"):
            monkeypatch.delenv(key, raising=False)
        with TestClient(create_app(Settings(), service=controlled_service)) as clean:
            assert clean.get(f"{V1}/health").status_code == 200
            assert clean.get(f"{V1}/ready").status_code == 200
            assert clean.get(f"{V1}/movies/{SPACE_QUERY_ID}").status_code == 200

    def test_settings_ignore_a_tmdb_token(self, monkeypatch):
        """Even if one is present, no setting absorbs it."""
        monkeypatch.setenv("TMDB_API_READ_ACCESS_TOKEN", "super-secret-token")
        settings = Settings()
        dumped = settings.model_dump_json()
        assert "super-secret-token" not in dumped
        assert "tmdb" not in dumped.lower()

    def test_creating_settings_never_reads_the_env_file(self, tmp_path, monkeypatch):
        """`.env` holds the TMDb credential; the API must not open it."""
        env_file = tmp_path / ".env"
        env_file.write_text("MOVIEMIND_MAX_K=99\nTMDB_API_READ_ACCESS_TOKEN=leak\n",
                            encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        assert Settings().max_k == 50  # not 99 -> the file was not read
        assert "leak" not in Settings().model_dump_json()

    def test_access_log_records_the_route_not_the_query(self, client, caplog):
        """A search term is user input and must not reach the log."""
        import logging

        with caplog.at_level(logging.INFO, logger="moviemind.api"):
            client.get(f"{V1}/movies/search", params={"q": "SENSITIVE-SEARCH-TERM"})
        logged = caplog.text
        assert "SENSITIVE-SEARCH-TERM" not in logged
        assert "/api/v1/movies/search" in logged
        assert "-> 200" in logged

    def test_access_log_records_the_route_not_the_movie_id(self, client, caplog):
        import logging

        with caplog.at_level(logging.INFO, logger="moviemind.api"):
            client.get(f"{V1}/movies/{SPACE_QUERY_ID}/recommendations")
        assert "/api/v1/movies/{movie_id}/recommendations" in caplog.text

    def test_only_get_is_routed(self, client):
        for method in ("post", "put", "patch", "delete"):
            response = getattr(client, method)(f"{V1}/health")
            assert response.status_code == 405, method

    def test_responses_set_no_store_by_default(self, client):
        """No caching headers are asserted as present; only that we add none
        that would let a shared cache retain per-request results."""
        headers = {k.lower() for k in client.get(f"{V1}/movies/{SPACE_QUERY_ID}").headers}
        assert "set-cookie" not in headers


# ---------------------------------------------------------------------------
# 7. Integration against the real 5,000-film snapshot
# ---------------------------------------------------------------------------
class TestRealCatalogue:
    def test_ready_reports_5000_films(self, real_client):
        body = real_client.get(f"{V1}/ready").json()
        assert body["catalogue"]["films"] == 5000
        assert body["catalogue"]["dimensions"] == 23404

    def test_geometry_matches_phase_4(self, real_client):
        stats = real_client.get(f"{V1}/ready").json()["catalogue"]
        assert stats["nonzeros"] == 226352
        assert stats["density_percent"] == pytest.approx(0.1934, abs=1e-3)

    def test_details_for_a_known_film(self, real_client):
        body = real_client.get(f"{V1}/movies/{REAL_MOVIE_ID}").json()
        assert body["movie_id"] == REAL_MOVIE_ID
        assert body["title"] == "The Shawshank Redemption"
        assert body["release_year"] == 1994

    def test_recommendations_fill_ten_slots(self, real_client):
        """The headline Phase 4 production number, through the API."""
        body = real_client.get(
            f"{V1}/movies/{REAL_MOVIE_ID}/recommendations", params={"k": 10}
        ).json()
        assert len(body["recommendations"]) == 10
        assert body["evidence"]["exhausted"] is False
        assert all(r["shared_terms"] >= 3 for r in body["recommendations"])

    def test_recommendations_match_the_engine_exactly(self, real_client):
        """The API must not reorder, re-filter or re-rank anything."""
        via_api = real_client.get(
            f"{V1}/movies/{REAL_MOVIE_ID}/recommendations", params={"k": 10}
        ).json()
        # The same service instance the routes used, so this compares the HTTP
        # layer against the engine rather than loading the corpus a second time.
        service = real_client.app.state.service
        direct = service.recommend(REAL_MOVIE_ID, 10)
        assert [r["movie_id"] for r in via_api["recommendations"]] == [
            r.movie_id for r in direct.recommendations
        ]
        assert [r["score"] for r in via_api["recommendations"]] == [
            r.score for r in direct.recommendations
        ]
        assert [r["rank"] for r in via_api["recommendations"]] == [
            r.rank for r in direct.recommendations
        ]
        assert [r["shared_terms"] for r in via_api["recommendations"]] == [
            r.shared_terms for r in direct.recommendations
        ]

    def test_search_finds_a_real_title(self, real_client):
        body = real_client.get(f"{V1}/movies/search", params={"q": "shawshank"}).json()
        assert REAL_MOVIE_ID in {r["movie_id"] for r in body["results"]}

    def test_films_requiring_recommendations_are_recommendable(self, real_client):
        """All 5,000 real films have document_size > 0 (Phase 3 finding), so the
        all-zero-vector 422 is a fixture-only path, not a live one."""
        body = real_client.get(f"{V1}/movies/{REAL_MOVIE_ID}").json()
        assert body["document_size"] > 0
        assert body["recommendable"] is True

    def test_determinism_across_fresh_app_instances(self):
        """Two independent loads must produce identical bytes."""
        if not PROCESSED_CORPUS.exists():
            pytest.skip("processed corpus absent")
        path = f"{V1}/movies/{REAL_MOVIE_ID}/recommendations"
        with TestClient(create_app(Settings())) as a, TestClient(create_app(Settings())) as b:
            assert a.get(path, params={"k": 10}).json() == b.get(path, params={"k": 10}).json()
